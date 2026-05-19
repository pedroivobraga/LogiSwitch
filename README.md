# LogiSwitch

DIY alternative to Logitech Flow using HID++ `CHANGE_HOST` (feature 0x1814).

Switches Logitech multi-device (Easy-Switch) mice/keyboards between paired hosts
when the mouse cursor reaches a screen edge — without needing LAN connectivity
between the two PCs. Useful when one PC has corporate firewall/EDR blocking
inbound traffic, which makes the real Flow refuse to work.

## How it works

Logitech Flow relies on bidirectional LAN traffic: a small "switch channel"
signal is sent over the network, and each device's Easy-Switch is triggered.
The mouse/keyboard data itself stays on the local Bluetooth/Bolt radio link,
which is what gives Flow its native-feeling latency.

LogiSwitch replicates only the trigger half: each PC watches its own cursor
locally, and when the cursor hits a screen edge for a configurable hold
duration, it sends the `CHANGE_HOST` HID++ command directly to the device's
radio (BT or USB receiver). The device switches Easy-Switch channels, and
its keystrokes/movement now flow to the other PC.

No LAN traffic required — each PC operates independently. This means it works
even when the two PCs cannot reach each other on the network.

## Requirements

- Windows
- Python 3.8+
- `pip install hidapi`
- Logitech device(s) paired to both PCs via Easy-Switch channels
  - Supported transports: Bluetooth direct (`usage_page=0xFF43`) and
    Logi Bolt / Unifying receiver (`usage_page=0xFF00`)
- Logi Options+ stopped / disabled while running (it holds HID++ reports
  exclusively and will block this tool from receiving responses):
  ```powershell
  Get-Service *logi* | Set-Service -StartupType Disabled
  Get-Service *logi* | Stop-Service -Force
  Get-Process *logi* | Stop-Process -Force
  ```

## Usage

### Tray app (recommended)

```powershell
pythonw source\logiswitch\app.py
```

A system tray icon appears with a context menu:
- **Pausar / Retomar** — temporarily disable edge detection
- **Configuracoes...** — open the settings window (channel mapping, hold ms, etc.)
- **Re-detectar devices** — re-run discovery (use after pairing a new device)
- **Sair** — quit

Configuration is stored in `%APPDATA%\LogiSwitch\config.json` and applied
on save with hot-reload (no restart). Multi-monitor setups use the full
virtual screen bounds, so the edge is the outermost pixel across all
displays.

### CLI (no tray)

```powershell
python source\logiswitch\logi_switch.py list                                    # list interfaces
python source\logiswitch\logi_switch.py scan [VID]                              # list raw HID devices
python source\logiswitch\logi_switch.py discover                                # show CHANGE_HOST-capable targets
python source\logiswitch\logi_switch.py watch --edge right --target 0 --hold 80 # blocking watch loop
```

`--target` is 0-based: `0` = Easy-Switch channel 1, `1` = channel 2, `2` = channel 3.

Add `--debug` before the command to see raw HID++ TX/RX bytes.

## Project layout

```
source/
  logiswitch/
    __init__.py
    logi_switch.py    HID++ core + CLI
    config.py         dataclass + JSON persistence (%APPDATA%\LogiSwitch\config.json)
    settings_ui.py    tkinter settings window
    app.py            pystray tray app, watch coordinator
README.md
requirements.txt
.gitignore
```

Imports inside the package use plain module names (`import config as config_mod`,
`import logi_switch as ls`) so the scripts can be executed directly from the
package folder. Python adds the script's directory to `sys.path`, so running
`python source\logiswitch\app.py` or `pythonw source\logiswitch\app.py` works
without any extra `PYTHONPATH` setup.

## Caveats

- Cursor positioning on the receiving PC is not coordinated — the cursor
  appears wherever it was last on that PC. Logitech Flow's "cursor lands on
  the opposite edge" requires LAN coordination, which is intentionally not
  supported here.
- Clipboard sync is not supported (also requires LAN).
- Bluetooth keyboards sleep aggressively; the tool sends a periodic keep-alive
  HID++ ping to maintain responsiveness.
- After a device switches to another host, its HID handle on this PC becomes
  invalid. The watch loop detects this, closes the handle, and re-enumerates
  on the next reconnection.
