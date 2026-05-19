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

### List interfaces
```powershell
python logi_switch.py list
```

### Auto-discover and watch screen edge
```powershell
# On the PC to the LEFT, watching the right edge, switching to host 0:
python logi_switch.py watch --edge right --target 0 --hold 80

# On the PC to the RIGHT, watching the left edge, switching to host 1:
python logi_switch.py watch --edge left --target 1 --hold 80
```

`--target` is 0-based: `0` = Easy-Switch channel 1, `1` = channel 2, `2` = channel 3.

The watch mode auto-discovers all Logitech HID++ devices visible to this
PC (BT-paired devices each become a target; devices behind a USB receiver
are enumerated via `dev_idx` 1..6).

### Manual commands
```powershell
python logi_switch.py scan [VID]              # list all HID devices (or filtered by VID)
python logi_switch.py probe --interface N     # probe HID++ on interface N
python logi_switch.py devices --interface N   # show CHANGE_HOST-capable devices
python logi_switch.py info [dev_idx] --interface N
python logi_switch.py switch <host> [dev_idx] --interface N
```

Add `--debug` before the command to see raw HID++ TX/RX bytes.

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
