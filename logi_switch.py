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
FEAT_FEATURE_SET = 0x0001
FEAT_CHANGE_HOST = 0x1814
FEAT_HOSTS_INFO = 0x1815
FEAT_WIRELESS_DEVICE_STATUS = 0x1D4B

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


def set_host(h, dev_idx, host_idx, feat_idx=None):
    """Dispara CHANGE_HOST.SetCurrentHost. Estilo Solaar: no_reply,
    fire-and-forget. Envia SHORT + LONG porque devices BT tem
    comportamento inconsistente sobre qual report aceita."""
    if feat_idx is None:
        feat_idx = get_feature_index(h, dev_idx, FEAT_CHANGE_HOST)
        if feat_idx is None:
            return False, 'CHANGE_HOST nao suportado / sem resposta'
    fn_swid = (1 << 4) | SW_ID
    short_pkt = bytes([REPORT_SHORT, dev_idx, feat_idx, fn_swid, host_idx, 0, 0])
    long_pkt = bytes([REPORT_LONG, dev_idx, feat_idx, fn_swid, host_idx]
                     + [0] * 15)
    sent_any = False
    err = None
    try:
        h.write(short_pkt)
        sent_any = True
    except Exception as e:
        err = e
    try:
        h.write(long_pkt)
        sent_any = True
    except Exception as e:
        err = err or e
    if sent_any:
        return True, 'sent'
    return False, f'write error: {err}'


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
                target = {
                    'name': product, 'product_id': pid, 'usage_page': usage,
                    'dev_idx': 0x00, 'handle': h,
                    'change_host_feat_idx': res['feat_idx'],  # cache - evita lookup no switch
                    'num_hosts': res['num_hosts'],
                    'current_host': res['current_host'],
                }
                # Pra teclados: tenta habilitar notificacao de host change
                if 'keyboard' in product.lower():
                    target.update(enable_host_notifications(target))
                targets.append(target)
                if verbose:
                    extra = ''
                    if 'hosts_info_feat_idx' in target:
                        extra += f" HOSTS_INFO=0x{target['hosts_info_feat_idx']:02X}"
                    if 'wireless_status_feat_idx' in target:
                        extra += f" WS=0x{target['wireless_status_feat_idx']:02X}"
                    print(f"  + {product} (BT, hosts={res['num_hosts']}, current={res['current_host']}){extra}")
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
                        'change_host_feat_idx': res['feat_idx'],  # cache
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


# ===== Subscription pra notificacoes de host change =====

def enable_host_notifications(target):
    """Tenta varias estrategias pra fazer o device emitir notificacao quando
    o usuario aperta F1/F2/F3. Retorna dict com 'hosts_info_feat_idx' e
    'wireless_status_feat_idx' se descobertos."""
    h = target['handle']
    if h is None:
        return {}
    di = target['dev_idx']
    info = {}

    # Estrategia 1: probar HOSTS_INFO (0x1815). Solaar usa essa feature
    # pra get_host_friendly_name; algumas implementacoes emitem evento na
    # primeira chamada.
    hi_idx = get_feature_index(h, di, FEAT_HOSTS_INFO)
    if hi_idx:
        info['hosts_info_feat_idx'] = hi_idx
        _dbg(f"  enable_notifications {target['name']}",
             f"HOSTS_INFO disponivel em feat_idx=0x{hi_idx:02X}")
        # Trigger a get para potencialmente "ativar" eventos
        hidpp_call(h, di, hi_idx, 0, b'', timeout_ms=200)

    # Estrategia 2: WIRELESS_DEVICE_STATUS (0x1D4B). Emite eventos quando
    # status do device muda - incluindo troca de host em alguns firmwares.
    ws_idx = get_feature_index(h, di, FEAT_WIRELESS_DEVICE_STATUS)
    if ws_idx:
        info['wireless_status_feat_idx'] = ws_idx
        _dbg(f"  enable_notifications {target['name']}",
             f"WIRELESS_DEVICE_STATUS disponivel em feat_idx=0x{ws_idx:02X}")

    # Estrategia 3: HID++ 1.0 - habilita 'wireless notifications' via
    # register 0x00 (set_register). Pode nao funcionar em BT direto mas
    # custa pouco tentar. Bit 0 = 'wireless notifications'.
    try:
        # Set register 0x00 = 00 10 00 (enable HID++ wireless notifications)
        pkt = bytes([REPORT_SHORT, di, 0x80, 0x00, 0x00, 0x10, 0x00])
        h.write(pkt)
        _dbg(f"  enable_notifications {target['name']}",
             "HID++1.0 set_register 0x00 = 00 10 00 enviado")
    except Exception:
        pass

    return info


# ===== Notification listener (deteccao de F1/F2/F3 no teclado) =====

def is_keyboard(target) -> bool:
    """Heuristica simples por nome - K850/MX Keys/etc tem 'keyboard' no
    product_string da Logitech."""
    return 'keyboard' in target.get('name', '').lower()


def parse_host_notification(report: bytes, accepted_feat_idxs: set) -> int | None:
    """Tenta extrair 'novo host' de uma notificacao HID++. Retorna o
    host_idx (0-based) ou None se nao for evento de host change.

    accepted_feat_idxs: set de feature indices que podem emitir host change
    (CHANGE_HOST e HOSTS_INFO, valores cacheados na descoberta)."""
    if len(report) < 5:
        return None
    if report[0] not in (REPORT_SHORT, REPORT_LONG):
        return None
    if (report[3] & 0x0F) != 0:
        return None  # nao e notificacao (sw_id deve ser 0)
    if report[2] not in accepted_feat_idxs:
        return None
    # Byte 4 e o candidato mais comum pro novo host. Se inválido, tenta byte 5.
    for cand in (report[4], report[5]):
        if cand <= 2:
            return cand
    return None


def poll_notifications(target):
    """Read nao-bloqueante (5ms) procurando notificacoes HID++ no device.
    Retorna list de bytes (raw reports nao identificados como response
    da nossa SW_ID)."""
    if target['handle'] is None:
        return []
    out = []
    # Drena ate 5 reports por iteracao pra evitar acumulo
    for _ in range(5):
        try:
            r = target['handle'].read(32, timeout_ms=2)
        except Exception:
            return out
        if not r:
            break
        r = bytes(r)
        if len(r) < 4:
            continue
        if r[0] not in (REPORT_SHORT, REPORT_LONG):
            continue
        # Filtra responses da nossa propria SW_ID - aquelas sao consumidas
        # pelo hidpp_call quando ele e chamado. Aqui pegamos so notificacoes.
        if (r[3] & 0x0F) == SW_ID:
            continue
        out.append(r)
        _dbg(f"NOTIFICATION from {target['name']}", r)
    return out


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
    last_fire_ts = 0.0  # quando rodamos CHANGE_HOST por edge - pra ignorar
                        # eventos que sao consequencia da nossa propria acao
    OURS_WINDOW = 4.0   # segundos apos edge-fire em que ignoramos disconnects

    # Classifica targets por tipo - so teclados originam mirroring
    keyboards = [t for t in targets if is_keyboard(t)]
    mice = [t for t in targets if not is_keyboard(t)]

    # Deteccao de saida: enumera periodicamente. Se um teclado some, o
    # usuario apertou F2/F3 no teclado e ele foi pro outro PC.
    last_enumerate_check = 0.0
    ENUMERATE_INTERVAL_S = 1.0
    keyboard_was_present = {kb['name']: True for kb in keyboards}
    keyboard_miss_count = {kb['name']: 0 for kb in keyboards}
    REQUIRED_MISSES = 2  # 2x miss = ~2s sem ver, evita falso positivo de hiccup BT

    def status(msg):
        if on_status:
            on_status(msg)

    def mirror_mice_to_host(new_host: int, source_name: str):
        """Espelha o new_host em todos os mice. Chamado quando detectamos
        que o usuario apertou F1/F2/F3 no teclado. Faz wake + retry pra
        cobrir mouse num estado meio zumbi devido a transicao BT."""
        nonlocal last_fire_ts
        status(f"{source_name}: host -> {new_host} (manual), espelhando mice")
        last_fire_ts = time.time()  # marca cedo: evita re-trigger em cascata
        for m in mice:
            if m['handle'] is None:
                status(f"  {m['name']}: handle invalido, pulando")
                continue
            # Wake antes do set_host pra garantir que o radio nao tá adormecido
            try:
                wake_burst(m['handle'], m['dev_idx'])
            except Exception:
                pass
            # Tenta 2 vezes com pausa entre - cobre o caso onde o primeiro
            # write cai durante o instante em que o BT esta servindo a
            # desconexao do teclado.
            sent_ok = False
            for attempt in range(2):
                try:
                    ok, msg = set_host(m['handle'], m['dev_idx'], new_host,
                                       feat_idx=m.get('change_host_feat_idx'))
                    sent_ok = sent_ok or ok
                    status(f"  {m['name']}: attempt {attempt + 1} -> {msg}")
                except Exception as e:
                    status(f"  {m['name']}: attempt {attempt + 1} ERR {e}")
                if attempt == 0:
                    time.sleep(0.15)
            _invalidate(m)
            if not sent_ok:
                status(f"  {m['name']}: TODOS os attempts falharam")

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

        # Deteccao de saida: enumera HIDs e detecta quando teclado some.
        # Quando o usuario aperta F2/F3 no K850, ele desconecta do BT deste
        # PC e some da enumeracao do Windows. A gente vê isso e espelha o
        # mouse pra mesma direcao.
        if now_s - last_enumerate_check >= ENUMERATE_INTERVAL_S:
            last_enumerate_check = now_s
            present_pids = set()
            try:
                for d in hid.enumerate(LOGI_VID, 0):
                    if d.get('usage_page') in HIDPP_USAGE_PAGES:
                        present_pids.add(d['product_id'])
            except Exception:
                present_pids = None  # se enumeracao falhar, pula esse ciclo

            if present_pids is not None:
                for kb in keyboards:
                    currently_present = kb['product_id'] in present_pids
                    if currently_present:
                        keyboard_miss_count[kb['name']] = 0
                        keyboard_was_present[kb['name']] = True
                        continue
                    keyboard_miss_count[kb['name']] += 1
                    if (keyboard_was_present[kb['name']]
                            and keyboard_miss_count[kb['name']] >= REQUIRED_MISSES):
                        # Saida confirmada. Foi nossa ou manual?
                        if now_s - last_fire_ts > OURS_WINDOW:
                            # Manual - teclado foi pro outro PC pela borda fisica
                            status(f"{kb['name']} desapareceu (F-key manual)")
                            mirror_mice_to_host(cfg.target_host_idx, kb['name'])
                            _invalidate(kb)
                        keyboard_was_present[kb['name']] = False

        # Edge detection (virtual screen, abrange todos os monitores).
        # Pode ser desativado via config se o usuario prefere usar so o
        # botao do teclado pra trocar (deteccao de saida cobre esse caso).
        if not getattr(cfg, 'edge_trigger_enabled', True):
            stuck = None
            armed = True
            time.sleep(0.01)
            continue

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
                # Wake-burst em todos (best-effort, nao gating).
                for t in targets:
                    if t['handle'] is None:
                        continue
                    wake_burst(t['handle'], t['dev_idx'])
                # CHANGE_HOST sempre tentado: usa feat_idx cacheado da descoberta,
                # estilo Solaar - no-reply write, nao espera ack.
                results = []
                for t in targets:
                    if t['handle'] is None:
                        results.append((t['name'], False, 'invalido'))
                        continue
                    try:
                        ok, msg = set_host(
                            t['handle'], t['dev_idx'], cfg.target_host_idx,
                            feat_idx=t.get('change_host_feat_idx'),
                        )
                        results.append((t['name'], ok, msg))
                    except Exception as e:
                        results.append((t['name'], False, f'ERR({e})'))
                    # Apos CHANGE_HOST o device sai do radio deste PC. O handle
                    # vira zumbi mesmo o write tendo retornado sucesso. Invalida
                    # sempre - reopen pega quando o usuario trouxer de volta.
                    _invalidate(t)
                last_fire_ts = now_s  # suprime notificacoes-eco do nosso fire
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
