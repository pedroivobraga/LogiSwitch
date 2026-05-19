# LogiSwitch

DIY alternative to Logitech Flow using HID++ `CHANGE_HOST` (feature `0x1814`).

Switches Logitech multi-device (Easy-Switch) mice/keyboards between paired hosts
when the mouse cursor reaches a screen edge — **without needing LAN connectivity
between the two PCs**. Useful when one PC has corporate firewall/EDR blocking
inbound traffic, which makes the real Logitech Flow refuse to work.

## How it works

Logitech Flow relies on bidirectional LAN traffic: a small "switch channel"
signal is sent over the network, and each device's Easy-Switch is triggered.
The mouse/keyboard data itself stays on the local Bluetooth / Bolt radio link,
which is what gives Flow its native-feeling latency.

LogiSwitch replicates only the trigger half: each PC watches its own cursor
locally, and when the cursor hits a screen edge for a configurable hold
duration, it sends the `CHANGE_HOST` HID++ command directly to the device's
radio (BT or USB receiver). The device switches Easy-Switch channels and its
input now flows to the other PC.

No LAN traffic required — each PC operates independently, so it works even
when the two PCs cannot reach each other on the network.

## Requirements

- Windows 10/11
- Python 3.9+
- `pip install -r requirements.txt` (hidapi, pystray, Pillow)
- Logitech device(s) paired to both PCs via Easy-Switch channels
  - Supported transports: Bluetooth direct (`usage_page=0xFF43`) and
    Logi Bolt / Unifying receiver (`usage_page=0xFF00`)
- **Logi Options+ stopped / disabled while running.** It opens the same
  HID++ vendor reports we need and starves our responses, so the keyboard
  appears unresponsive. The simplest reliable kill:
  ```powershell
  Get-Service *logi* -ErrorAction SilentlyContinue | Set-Service -StartupType Disabled
  Get-Service *logi* -ErrorAction SilentlyContinue | Stop-Service -Force
  Get-Process *logi*  -ErrorAction SilentlyContinue | Stop-Process -Force
  ```

## Usage

### Tray app (recommended)

```powershell
pythonw app.py
```

A system tray icon appears (green when active, gray when paused, with the
current channel number printed on it). Right-click for the menu:

- **Pausar / Retomar** — temporarily disable edge detection
- **Configuracoes...** — open the settings window (channel mapping, hold/cooldown)
- **Re-detectar devices** — re-run discovery (use after pairing a new device)
- **Iniciar com Windows** — toggle autostart (writes/removes a value under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`)
- **Sair** — quit

Configuration is stored in `%APPDATA%\LogiSwitch\config.json` and applied
on save with **hot-reload** (no restart). Multi-monitor setups use the full
virtual screen bounds, so the trigger edge is the outermost pixel across all
displays combined.

### CLI (no tray, useful for debugging)

```powershell
python logi_switch.py list                                          # list HID++ interfaces
python logi_switch.py scan [VID]                                    # list raw HID devices
python logi_switch.py discover                                      # show CHANGE_HOST-capable targets
python logi_switch.py watch --edge right --target 1 --hold 80       # blocking watch loop
```

`--target` is 0-based: `0` = Easy-Switch channel 1, `1` = channel 2, `2` = channel 3.

Add `--debug` before any command to print raw HID++ TX/RX bytes:

```powershell
python logi_switch.py --debug watch --edge right --target 1 --hold 80
```

## Project layout

```
logi_switch.py    HID++ core (open/probe/CHANGE_HOST) + CLI
config.py         Config dataclass + JSON persistence (%APPDATA%\LogiSwitch\config.json)
settings_ui.py    tkinter settings window (channel mapping, hold/cooldown)
autostart.py      Toggle for HKCU\...\Run autostart entry
app.py            pystray tray app, watch_loop coordinator, settings hot-reload
```

## Implementation notes

A few hard-won lessons documented for future maintainers:

- **Bluetooth-direct devices use `usage_page=0xFF43`**, not `0xFF00` (which is
  the USB-receiver vendor page). The discovery filter accepts both.
- **`CHANGE_HOST` is fire-and-forget.** Following Solaar's lead
  (`no_reply: True`), the tool does not wait for an ack — the device may
  already be leaving this host's radio when an ack would have been due.
- **Send both SHORT (`0x10`) and LONG (`0x11`) reports.** Some BT firmwares
  only process one of the two for vendor writes; sending both back-to-back
  costs almost nothing and avoids guessing per-model.
- **Feature index is cached** at discovery time. Re-querying ROOT.GetFeature
  on every switch competes with the keyboard's input report stream and
  intermittently times out — looking like "CHANGE_HOST not supported" when
  the device is actually fine.
- **No periodic keep-alive.** A 1.5s background ping kept devices responsive
  but wasted battery for the sake of a sub-second wake at edge time. Instead
  the tool does a one-shot `wake_burst` at the moment of the switch: probe
  first (~80ms if already awake), and only fall through to burst writes
  (~1.5s worst case) if the device is in deep sleep.
- **Multi-monitor edge** uses `GetSystemMetrics(SM_X/CXVIRTUALSCREEN)` so the
  trigger is the outermost pixel across all displays, not just the primary.
- **Reopen after switch.** After `CHANGE_HOST`, the device's HID handle on
  this PC goes stale. The watch loop closes it, re-enumerates via
  `hid.enumerate(VID, PID)` to get a fresh path, and validates the new
  handle with a probe before considering the device "reconnected".

## Caveats

- **Cursor positioning on the receiving PC is not coordinated.** The cursor
  on the target PC appears wherever it was last — Flow's "cursor lands on
  the opposite edge" requires LAN coordination, which is intentionally not
  supported here.
- **Clipboard sync is not supported** (also requires LAN).
- **Real Flow already has Logi Options+ doing keep-alive**, which is why
  pausing/killing Options+ is a hard requirement when running this tool.
- **Worst-case ~1.5s** delay on the very first switch after a long idle
  period, while the keyboard's BT radio fully wakes from deep sleep. After
  that, switches are sub-200ms.

## Credits

Reverse-engineered against the [Solaar](https://github.com/pwr-Solaar/Solaar)
codebase, which is the authoritative open-source reference for HID++ on
Logitech devices.
