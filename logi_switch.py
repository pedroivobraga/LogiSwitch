"""
logi_switch.py - DIY Logitech Flow via HID++ CHANGE_HOST (0x1814)

Dual use:
  - Library: import discover_targets, watch_loop, etc. from app.py / tray
  - CLI: run as script for manual testing

CLI commands:
    python logi_switch.py [--debug] list
    python logi_switch.py [--debug] scan [VID]
    python logi_switch.py [--debug] discover
    python logi_switch.py [--debug] watch --edge {left|right} --target H [--hold MS]
"""

import ctypes
import struct
import sys
import threading
import time

import hid

LOGI_VID = 0x046D
HIDPP_USAGE_PAGES = (0xFF00, 0xFF43)

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
SW_ID = 0x09

FEAT_ROOT = 0x0000
FEAT_CHANGE_HOST = 0x1814

ERR_HIDPP10 = 0x8F
ERR_HIDPP20 = 0xFF

DEBUG = False


def _dbg(prefix, data):
    if DEBUG:
        if isinstance(data, (bytes, bytearray)):
            hexstr = ' '.join(f"{b:02X}" for b in data)
        else:
            hexstr = str(data)
        print(f"  {prefix}: {hexstr}")


# ===== HID++ =====

def find_hidpp_interfaces():
    return [d for d in hid.enumerate(LOGI_VID, 0)
            if d.get('usage_page') in HIDPP_USAGE_PAGES]


def open_device(path):
    h = hid.device()
    h.open_path(path)
    h.set_nonblocking(0)
    return h


def hidpp_call(h, dev_idx, feat_idx, func_id, params=b'',
               report=REPORT_SHORT, timeout_ms=600):
    """Envia HID++ e tenta ler resposta. Em BT, retry automatico em LONG."""
    for attempt_report in ([report] if report == REPORT_LONG else [REPORT_SHORT, REPORT_LONG]):
        size = 7 if attempt_report == REPORT_SHORT else 20
        payload = bytes([attempt_report, dev_idx, feat_idx,
                         (func_id << 4) | SW_ID]) + params
        pad = size - len(payload)
        if pad < 0:
            continue
        payload += b'\x00' * pad
        _dbg(f"TX[{'SHORT' if attempt_report == REPORT_SHORT else 'LONG'}]", payload)
        try:
            h.write(payload)
        except Exception as e:
            _dbg("write error", str(e))
            return None
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            rem = max(1, int((deadline - time.time()) * 1000))
            try:
                r = h.read(32, timeout_ms=rem)
            except Exception as e:
                _dbg("read error", str(e))
                return None
            if not r:
                continue
            r = bytes(r)
            _dbg("RX", r)
            if len(r) < 4:
                continue
            if r[0] not in (REPORT_SHORT, REPORT_LONG):
                continue
            if r[1] != dev_idx:
                continue
            if r[2] in (ERR_HIDPP10, ERR_HIDPP20):
                return r
            if (r[3] & 0x0F) == SW_ID and (r[3] >> 4) == func_id and r[2] == feat_idx:
                return r
    return None


def get_feature_index(h, dev_idx, feature_id):
    resp = hidpp_call(h, dev_idx, 0x00, 0, struct.pack(">H", feature_id))
    if not resp or resp[2] in (ERR_HIDPP10, ERR_HIDPP20):
        return None
    idx = resp[4]
    return idx if idx else None


def get_hosts_info(h, dev_idx):
    feat_idx = get_feature_index(h, dev_idx, FEAT_CHANGE_HOST)
    if feat_idx is None:
        return None
    resp = hidpp_call(h, dev_idx, feat_idx, 0)
    if not resp or resp[2] in (ERR_HIDPP10, ERR_HIDPP20):
        return None
    return {'feat_idx': feat_idx, 'num_hosts': resp[4], 'current_host': resp[5]}


def set_host(h, dev_idx, host_idx, retries=2):
    """Tenta CHANGE_HOST com retry. Em teclados BT que dormem, a primeira
    tentativa as vezes so acorda o radio sem efetuar a troca."""
    for attempt in range(retries + 1):
        feat_idx = get_feature_index(h, dev_idx, FEAT_CHANGE_HOST)
        if feat_idx is None:
            if attempt < retries:
                # device pode estar dormindo - espera e tenta de novo
                time.sleep(0.1)
                continue
            return False, 'CHANGE_HOST nao suportado / sem resposta'
        resp = hidpp_call(h, dev_idx, feat_idx, 1, bytes([host_idx]), timeout_ms=300)
        if resp is None:
            return True, 'sent (sem ack)'
        if resp[2] in (ERR_HIDPP10, ERR_HIDPP20):
            return False, f'erro 0x{resp[4]:02X}'
        return True, 'ack'


def keep_awake(h, dev_idx):
    try:
        payload = bytes([REPORT_SHORT, dev_idx, 0x00, (1 << 4) | SW_ID, 0, 0, 0])
        h.write(payload)
        h.read(32, timeout_ms=5)
        return True
    except Exception as e:
        _dbg("keep_awake error", str(e))
        return False


def wake_burst(h, dev_idx, max_rounds=3, pings_per_round=8, gap_ms=50):
    """Probe rapido (~80ms) confirma se ja awake. Se nao, faz rounds de
    burst (~400ms cada) + re-probe ate confirmar. Total max: ~1.5s pra
    deep sleep. Devolve True se confirmou awake, False se desistiu."""
    # 1. Probe inicial (caso awake/shallow sleep)
    if hidpp_call(h, dev_idx, 0x00, 1, b'\x00\x00\x00', timeout_ms=40) is not None:
        return True
    # 2. Rounds de burst + re-probe (caso deep sleep)
    for _ in range(max_rounds):
        for _ in range(pings_per_round):
            keep_awake(h, dev_idx)
            time.sleep(gap_ms / 1000)
        if hidpp_call(h, dev_idx, 0x00, 1, b'\x00\x00\x00', timeout_ms=80) is not None:
            return True
    return False


def find_fresh_path(product_id, usage_page):
    for d in hid.enumerate(LOGI_VID, product_id):
        if d.get('usage_page') == usage_page:
            return d['path']
    return None


def reopen_handle(product_id, usage_page, dev_idx):
    """Re-enumera + valida com probe. Retorna (handle, path) ou (None, None)."""
    path = find_fresh_path(product_id, usage_page)
    if not path:
        return None, None
    try:
        h = hid.device()
        h.open_path(path)
        h.set_nonblocking(0)
    except Exception as e:
        _dbg("reopen open_path error", str(e))
        return None, None
    resp = hidpp_call(h, dev_idx, 0x00, 1, b'\x00\x00\x00', timeout_ms=300)
    if resp is None:
        try: h.close()
        except: pass
        return None, None
    return h, path


# ===== Discovery =====

def _probe_with_retry(h, dev_idx, attempts=3, delay_s=0.2):
    """Wake + probe ate get_hosts_info responder. Util pra descoberta
    de devices BT que podem estar ocupados entregando input."""
    for i in range(attempts):
        wake_burst(h, dev_idx)  # internamente probe-first + burst se preciso
        res = get_hosts_info(h, dev_idx)
        if res:
            return res
        if i < attempts - 1:
            time.sleep(delay_s)
    return None


def discover_targets(verbose=False):
    """Lista de targets: dicts com name, product_id, usage_page, dev_idx, handle.
    BT (0xFF43): cada interface = 1 target dev_idx=0x00.
    USB receiver (0xFF00): scan dev_idx 1..6."""
    targets = []
    for info in find_hidpp_interfaces():
        try:
            h = open_device(info['path'])
        except Exception as e:
            if verbose: print(f"Falha abrindo {info.get('product_string','?')}: {e}")
            continue

        usage = info.get('usage_page')
        pid = info['product_id']
        product = info.get('product_string', f"PID_{pid:04X}")

        if usage == 0xFF43:
            res = _probe_with_retry(h, 0x00)
            if res:
                targets.append({
                    'name': product, 'product_id': pid, 'usage_page': usage,
                    'dev_idx': 0x00, 'handle': h,
                    'num_hosts': res['num_hosts'],
                    'current_host': res['current_host'],
                })
                if verbose:
                    print(f"  + {product} (BT, hosts={res['num_hosts']}, current={res['current_host']})")
            else:
                if verbose: print(f"  - {product} (BT): sem resposta")
                try: h.close()
                except: pass

        elif usage == 0xFF00:
            found = False
            for di in range(1, 7):
                res = _probe_with_retry(h, di, attempts=2, delay_s=0.15)
                if res:
                    found = True
                    try:
                        th = open_device(info['path'])
                    except Exception:
                        continue
                    targets.append({
                        'name': f"{product}/dev{di}", 'product_id': pid,
                        'usage_page': usage, 'dev_idx': di, 'handle': th,
                        'num_hosts': res['num_hosts'],
                        'current_host': res['current_host'],
                    })
                    if verbose:
                        print(f"  + {product}/dev{di} (USB, hosts={res['num_hosts']}, current={res['current_host']})")
            try: h.close()
            except: pass
            if not found and verbose:
                print(f"  - {product} (USB): sem pareados")

    return targets


# ===== Multi-monitor virtual screen =====

if sys.platform == 'win32':
    _user32 = ctypes.windll.user32
else:
    _user32 = None

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor():
    p = POINT()
    _user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def virtual_screen_bounds():
    """(xmin, ymin, xmax, ymax) cobrindo TODOS os monitores."""
    x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, x + w - 1, y + h - 1


# ===== Watch loop (API pra tray/app) =====

def _invalidate(t):
    if t.get('handle') is not None:
        try:
            t['handle'].close()
        except Exception:
            pass
    t['handle'] = None


def watch_loop(targets, cfg, stop_event, on_switch=None, on_status=None):
    """Loop principal. Le cfg dinamicamente (hot reload de edge/target/etc).

    cfg precisa expor: edge, target_host_idx, hold_ms, cooldown_ms, paused.
    stop_event: threading.Event - encerra quando setado.
    on_switch(target_host, results): callback apos cada disparo.
    on_status(msg): callback opcional pra status (reconectado etc).
    """
    if _user32 is None:
        raise RuntimeError("watch_loop requer Windows")

    stuck = None
    armed = True  # so re-dispara apos cursor sair da borda
    last_reopen = {t['name']: 0.0 for t in targets}
    reopen_s = 2.0

    def status(msg):
        if on_status:
            on_status(msg)

    while not stop_event.is_set():
        if cfg.paused:
            time.sleep(0.1)
            stuck = None
            armed = True
            continue

        now_s = time.time()

        # Reabre handles invalidos (devices que voltaram apos switch anterior)
        for t in targets:
            if t['handle'] is None and now_s - last_reopen[t['name']] > reopen_s:
                h, _ = reopen_handle(t['product_id'], t['usage_page'], t['dev_idx'])
                if h is not None:
                    t['handle'] = h
                    status(f"{t['name']} reconectado")
                last_reopen[t['name']] = now_s

        # Edge detection (virtual screen, abrange todos os monitores)
        xmin, _ymin, xmax, _ymax = virtual_screen_bounds()
        x, _y = get_cursor()
        at_edge = (x >= xmax) if cfg.edge == 'right' else (x <= xmin)

        if at_edge:
            if not armed:
                # Ja disparamos nesta visita a borda; espera sair pra rearmar
                time.sleep(0.01)
                continue
            now_ms = now_s * 1000
            if stuck is None:
                stuck = now_ms
            elif now_ms - stuck >= cfg.hold_ms:
                for t in targets:
                    if t['handle'] is None:
                        continue
                    if not wake_burst(t['handle'], t['dev_idx']):
                        _invalidate(t)
                results = []
                for t in targets:
                    if t['handle'] is None:
                        results.append((t['name'], False, 'invalido'))
                        continue
                    try:
                        ok, msg = set_host(t['handle'], t['dev_idx'], cfg.target_host_idx)
                        results.append((t['name'], ok, msg))
                        if not ok and 'sem resposta' in msg:
                            _invalidate(t)
                    except Exception as e:
                        results.append((t['name'], False, f'ERR({e})'))
                        _invalidate(t)
                if on_switch:
                    on_switch(cfg.target_host_idx, results)
                stuck = None
                armed = False  # bloqueia re-disparo ate sair da borda
                stop_event.wait(cfg.cooldown_ms / 1000)
        else:
            stuck = None
            armed = True

        time.sleep(0.01)


def close_targets(targets):
    """Fecha todos os handles - chamar antes de sair."""
    for t in targets:
        _invalidate(t)


# ===== CLI =====

def _parse_kv(argv, defaults):
    out = dict(defaults)
    i = 0
    while i < len(argv):
        k = argv[i].lstrip('-')
        v = argv[i + 1]
        out[k] = v if k == 'edge' else int(v)
        i += 2
    return out


def _cli_watch(argv):
    from dataclasses import dataclass

    opts = _parse_kv(argv, {'edge': 'right', 'target': 1, 'hold': 80})

    @dataclass
    class CliCfg:
        edge: str = opts['edge']
        target_host_idx: int = opts['target']
        hold_ms: int = opts['hold']
        cooldown_ms: int = 800
        paused: bool = False

    print("Descobrindo devices Logitech...")
    targets = discover_targets(verbose=True)
    if not targets:
        print("Nenhum target encontrado.")
        return

    print(f"Vigiando {opts['edge']}, target host {opts['target']}, hold {opts['hold']}ms. Ctrl+C pra parar.")
    stop = threading.Event()

    def cb(host, results):
        ts = time.strftime('%H:%M:%S')
        parts = [f"{name}={msg}" for name, _ok, msg in results]
        print(f"[{ts}] -> host {host}: {' | '.join(parts)}")

    def status(msg):
        print(f"  [{time.strftime('%H:%M:%S')}] {msg}")

    try:
        watch_loop(targets, CliCfg(), stop, on_switch=cb, on_status=status)
    except KeyboardInterrupt:
        stop.set()
    finally:
        close_targets(targets)


def main():
    global DEBUG
    argv = sys.argv[1:]
    if '--debug' in argv:
        DEBUG = True
        argv.remove('--debug')
    if not argv:
        print(__doc__); return

    cmd = argv[0]
    rest = argv[1:]

    if cmd == 'list':
        for i, d in enumerate(find_hidpp_interfaces()):
            print(f"[{i}] PID=0x{d['product_id']:04X} {d.get('product_string','?')} "
                  f"usage_page=0x{d.get('usage_page',0):04X}")
        return

    if cmd == 'scan':
        vid = int(rest[0], 0) if rest else 0
        for d in hid.enumerate(vid, 0):
            print(f"VID=0x{d['vendor_id']:04X} PID=0x{d['product_id']:04X} "
                  f"'{d.get('product_string','?')}' usage_page=0x{d.get('usage_page',0):04X}")
        return

    if cmd == 'discover':
        discover_targets(verbose=True)
        return

    if cmd == 'watch':
        _cli_watch(rest)
        return

    print(f"Comando desconhecido: {cmd}")
    print(__doc__)


if __name__ == '__main__':
    main()
