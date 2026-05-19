"""
logi_switch.py - DIY Logitech Flow via HID++ CHANGE_HOST (0x1814)

Setup (Windows):
    pip install hidapi
    Encerre Logi Options+ antes (servico tambem):
        Get-Service *logi* | Set-Service -StartupType Disabled
        Get-Service *logi* | Stop-Service -Force
        Get-Process *logi* | Stop-Process -Force

Comandos:
    python logi_switch.py [--debug] list
    python logi_switch.py [--debug] scan [VID]
    python logi_switch.py [--debug] probe --interface N
    python logi_switch.py [--debug] devices --interface N
    python logi_switch.py [--debug] info [dev_idx] --interface N
    python logi_switch.py [--debug] switch <host_idx> [dev_idx] --interface N
    python logi_switch.py [--debug] watch --edge {left|right} --target H [--hold MS]
"""

import ctypes
import struct
import sys
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
    """Envia um HID++ e tenta ler a resposta. Tolera disconnect (BT trocou de PC)."""
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
    params = struct.pack(">H", feature_id)
    resp = hidpp_call(h, dev_idx, 0x00, 0, params)
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


def set_host(h, dev_idx, host_idx):
    feat_idx = get_feature_index(h, dev_idx, FEAT_CHANGE_HOST)
    if feat_idx is None:
        return False, 'CHANGE_HOST nao suportado / sem resposta'
    resp = hidpp_call(h, dev_idx, feat_idx, 1, bytes([host_idx]), timeout_ms=300)
    if resp is None:
        return True, 'sent (sem ack)'
    if resp[2] in (ERR_HIDPP10, ERR_HIDPP20):
        return False, f'erro 0x{resp[4]:02X}'
    return True, 'ack'


def keep_awake(h, dev_idx):
    """Retorna True se write/read OK, False se device parece morto."""
    try:
        payload = bytes([REPORT_SHORT, dev_idx, 0x00, (1 << 4) | SW_ID, 0, 0, 0])
        h.write(payload)
        h.read(32, timeout_ms=5)
        return True
    except Exception as e:
        _dbg("keep_awake error", str(e))
        return False


def wake_burst(h, dev_idx, attempts=3):
    ok = False
    for _ in range(attempts):
        if keep_awake(h, dev_idx):
            ok = True
        time.sleep(0.02)
    return ok


# ===== Edge detection (Windows) =====

if sys.platform == 'win32':
    _user32 = ctypes.windll.user32
else:
    _user32 = None


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor():
    p = POINT()
    _user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def screen_size():
    return _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)


def find_fresh_path(product_id, usage_page):
    """Re-enumera HIDs e devolve o path atual deste device (post-reconnect
    pode ter mudado de path/Col)."""
    for d in hid.enumerate(LOGI_VID, product_id):
        if d.get('usage_page') == usage_page:
            return d['path']
    return None


def reopen_handle(product_id, usage_page, dev_idx):
    """Re-enumera e abre fresh. Valida o handle com um probe leve.
    Retorna (handle, path) ou (None, None)."""
    path = find_fresh_path(product_id, usage_page)
    if not path:
        _dbg("reopen", f"path nao encontrado pra PID 0x{product_id:04X}")
        return None, None
    try:
        h = hid.device()
        h.open_path(path)
        h.set_nonblocking(0)
    except Exception as e:
        _dbg("reopen open_path error", str(e))
        return None, None
    # Validacao: tenta um GetProtocolVersion - se nao responder, handle e zumbi
    resp = hidpp_call(h, dev_idx, 0x00, 1, b'\x00\x00\x00', timeout_ms=300)
    if resp is None:
        _dbg("reopen", "handle aberto mas nao responde probe - fechando")
        try:
            h.close()
        except Exception:
            pass
        return None, None
    return h, path


def _invalidate(d):
    if d.get('handle') is not None:
        try:
            d['handle'].close()
        except Exception:
            pass
    d['handle'] = None


def watch_edge(targets, target_host, edge='right',
               hold_ms=80, cooldown_ms=800, keepalive_s=3.0,
               reopen_s=2.0):
    """targets: lista de dicts {'name', 'product_id', 'usage_page', 'dev_idx', 'handle'}.
    Cada target tem seu proprio dev_idx (0x00 pra BT, 1..6 pra receptor USB)."""
    if _user32 is None:
        raise RuntimeError("watch_edge requer Windows")
    w, _ = screen_size()
    names = ", ".join(f"{t['name']}(di=0x{t['dev_idx']:02X})" for t in targets)
    print(f"Vigiando borda {edge} (tela={w}px). Hold {hold_ms}ms dispara.")
    print(f"Targets: {names}. Keep-alive a cada {keepalive_s}s. Ctrl+C pra parar.")

    stuck = None
    last_keepalive = 0
    last_reopen = {t['name']: 0 for t in targets}

    while True:
        now_s = time.time()

        # Reabre handles invalidos
        for t in targets:
            if t['handle'] is None and now_s - last_reopen[t['name']] > reopen_s:
                h, _ = reopen_handle(t['product_id'], t['usage_page'], t['dev_idx'])
                if h is not None:
                    t['handle'] = h
                    print(f"  [{time.strftime('%H:%M:%S')}] {t['name']} reconectado")
                last_reopen[t['name']] = now_s

        # Keep-alive
        if now_s - last_keepalive > keepalive_s:
            for t in targets:
                if t['handle'] is not None:
                    if not keep_awake(t['handle'], t['dev_idx']):
                        _invalidate(t)
                        print(f"  [{time.strftime('%H:%M:%S')}] {t['name']} keep-alive falhou")
            last_keepalive = now_s

        # Edge detection
        x, _y = get_cursor()
        at_edge = (x >= w - 1) if edge == 'right' else (x <= 0)
        if at_edge:
            now_ms = now_s * 1000
            if stuck is None:
                stuck = now_ms
            elif now_ms - stuck >= hold_ms:
                ts = time.strftime('%H:%M:%S')
                for t in targets:
                    if t['handle'] is None:
                        continue
                    if not wake_burst(t['handle'], t['dev_idx']):
                        _invalidate(t)
                results = []
                for t in targets:
                    if t['handle'] is None:
                        results.append(f"{t['name']}=invalido")
                        continue
                    try:
                        ok, msg = set_host(t['handle'], t['dev_idx'], target_host)
                        results.append(f"{t['name']}={msg}")
                        if not ok and 'sem resposta' in msg:
                            _invalidate(t)
                    except Exception as e:
                        results.append(f"{t['name']}=ERR({e})")
                        _invalidate(t)
                print(f"[{ts}] -> host {target_host}: {' | '.join(results)}")
                stuck = None
                time.sleep(cooldown_ms / 1000)
        else:
            stuck = None
        time.sleep(0.01)


def discover_targets():
    """Acha automaticamente todos os devices Logitech que suportam CHANGE_HOST.
    BT: cada interface 0xFF43 vira 1 target com dev_idx=0x00.
    USB receiver: interface 0xFF00, scan dev_idx 1..6, cada pareado vira 1 target."""
    targets = []
    for info in find_hidpp_interfaces():
        try:
            h = open_device(info['path'])
        except Exception as e:
            print(f"Falha abrindo {info.get('product_string','?')}: {e}")
            continue

        usage = info.get('usage_page')
        pid = info['product_id']
        product = info.get('product_string', f"PID_{pid:04X}")

        if usage == 0xFF43:
            # BT direto: device unico, dev_idx=0x00
            res = get_hosts_info(h, 0x00)
            if res:
                targets.append({
                    'name': product,
                    'product_id': pid,
                    'usage_page': usage,
                    'dev_idx': 0x00,
                    'handle': h,
                })
                print(f"  + {product} (BT, dev_idx=0x00, hosts={res['num_hosts']}, current={res['current_host']})")
            else:
                print(f"  - {product} (BT): nao respondeu HID++")
                try: h.close()
                except: pass

        elif usage == 0xFF00:
            # Receptor USB: scan dev_idx 1..6, handle separado por device
            found_any = False
            for di in range(1, 7):
                res = get_hosts_info(h, di)
                if res:
                    found_any = True
                    # Abre handle proprio pra este target (cross-handle e seguro:
                    # HID no Windows duplica reports pra todos os opens)
                    try:
                        th = open_device(info['path'])
                    except Exception as e:
                        print(f"    falha abrindo handle extra: {e}")
                        continue
                    targets.append({
                        'name': f"{product}/dev{di}",
                        'product_id': pid,
                        'usage_page': usage,
                        'dev_idx': di,
                        'handle': th,
                    })
                    print(f"  + {product}/dev{di} (USB, dev_idx={di}, hosts={res['num_hosts']}, current={res['current_host']})")
            # Handle de scan original nao e mais usado
            try: h.close()
            except: pass
            if not found_any:
                print(f"  - {product} (USB): nenhum device pareado responde")

    return targets


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


def main():
    global DEBUG
    argv = sys.argv[1:]
    if '--debug' in argv:
        DEBUG = True
        argv.remove('--debug')
    if not argv:
        print(__doc__); return

    cmd = argv[0]
    sys.argv = [sys.argv[0], cmd] + argv[1:]

    if cmd == 'list':
        ifs = find_hidpp_interfaces()
        if not ifs:
            print("Nenhuma interface HID++ Logitech encontrada.")
            return
        for i, d in enumerate(ifs):
            print(f"[{i}] PID=0x{d['product_id']:04X} {d.get('product_string','?')} "
                  f"iface={d.get('interface_number')} "
                  f"usage_page=0x{d.get('usage_page',0):04X} "
                  f"path={d['path']!r}")
        return

    if cmd == 'scan':
        vid_filter = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
        for d in hid.enumerate(vid_filter, 0):
            print(f"VID=0x{d['vendor_id']:04X} PID=0x{d['product_id']:04X} "
                  f"'{d.get('product_string','?')}' mfr='{d.get('manufacturer_string','?')}' "
                  f"iface={d.get('interface_number')} "
                  f"usage_page=0x{d.get('usage_page',0):04X} "
                  f"usage=0x{d.get('usage',0):04X}")
        return

    ifs = find_hidpp_interfaces()
    if not ifs:
        print("Nenhuma interface HID++ Logitech encontrada.")
        return

    def default_dev_idx(iface_info):
        return 0x00 if iface_info.get('usage_page') == 0xFF43 else 1

    if cmd == 'probe':
        iface_n = 0
        rest = list(sys.argv[2:])
        if '--interface' in rest:
            i = rest.index('--interface')
            iface_n = int(rest[i + 1])
        info = ifs[iface_n]
        print(f"Probing: {info.get('product_string','?')} (usage_page=0x{info['usage_page']:04X})")
        h = open_device(info['path'])
        for di in [0xFF, 0x00, 0x01]:
            print(f"\n-- dev_idx=0x{di:02X} --")
            print("  GetProtocolVersion (ROOT.func1):")
            r = hidpp_call(h, di, 0x00, 1, b'\x00\x00\x00')
            print(f"    -> {' '.join(f'{b:02X}' for b in r) if r else 'sem resposta'}")
            print("  GetFeature(CHANGE_HOST=0x1814):")
            r = hidpp_call(h, di, 0x00, 0, b'\x18\x14')
            if r:
                print(f"    -> {' '.join(f'{b:02X}' for b in r)}")
                if r[2] not in (ERR_HIDPP10, ERR_HIDPP20) and r[4] != 0:
                    print(f"    *** CHANGE_HOST em feat_idx=0x{r[4]:02X} ***")
            else:
                print("    -> sem resposta")
        return

    if cmd in ('devices', 'info', 'switch'):
        iface_n = 0
        rest = list(sys.argv[2:])
        if '--interface' in rest:
            i = rest.index('--interface')
            iface_n = int(rest[i + 1])
            del rest[i:i + 2]

        info = ifs[iface_n]
        print(f"Usando: PID=0x{info['product_id']:04X} {info.get('product_string','?')} "
              f"(usage_page=0x{info['usage_page']:04X})")
        h = open_device(info['path'])
        default_di = default_dev_idx(info)

        if cmd == 'devices':
            candidates = [0x00, 0xFF, 0x01] if default_di == 0x00 else list(range(1, 7)) + [0xFF]
            any_found = False
            for di in candidates:
                res = get_hosts_info(h, di)
                if res:
                    any_found = True
                    print(f"  dev_idx=0x{di:02X}: feat_idx=0x{res['feat_idx']:02X} "
                          f"hosts={res['num_hosts']} current={res['current_host']}")
            if not any_found:
                print("Nenhum device respondeu CHANGE_HOST.")
            return

        if cmd == 'info':
            di = int(rest[0], 0) if rest else default_di
            res = get_hosts_info(h, di)
            print(res if res else f"dev_idx=0x{di:02X}: sem resposta")
            return

        if cmd == 'switch':
            host = int(rest[0])
            di = int(rest[1], 0) if len(rest) > 1 else default_di
            wake_burst(h, di, attempts=5)
            ok, msg = set_host(h, di, host)
            print(f"dev_idx=0x{di:02X} -> host {host}: {msg}")
            return

    if cmd == 'watch':
        opts = _parse_kv(sys.argv[2:],
                         {'edge': 'right', 'target': 1, 'hold': 80})
        print("Descobrindo devices Logitech compativeis...")
        targets = discover_targets()
        if not targets:
            print("Nenhum target encontrado (BT direto ou receptor USB).")
            return
        try:
            watch_edge(targets, opts['target'],
                       edge=opts['edge'], hold_ms=opts['hold'])
        except KeyboardInterrupt:
            print()
        return

    print(f"Comando desconhecido: {cmd}")
    print(__doc__)


if __name__ == '__main__':
    main()
