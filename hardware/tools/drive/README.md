# Drive the iPhone from a Mac (Bluetooth HID board)

Experimental, for the `feat/ble-hid` firmware. The board stays on the Mac's USB port for power and serial commands;
it drives the iPhone as a Bluetooth mouse and keyboard, and the Mac watches the screen over the board's Wi-Fi video.
Use this instead of the `esp32agent` CLI when its video over Bluetooth lags (about 35 kbit/s, minutes behind).

```
Mac ──USB serial──▶ board ──Bluetooth HID──▶ iPhone
 ▲                    ▲                        │
 └── Wi-Fi TCP 7201 ──┴──── Screen Mirroring ──┘   (board in station mode on the Mac's Wi-Fi)
```

## Once

1. Flash the test build, Bluetooth even with USB plugged in: `hardware/flash.sh --debug --ble-hid`.
2. iPhone: pair **ESP32 Agent XXXX** in Settings → Bluetooth within 3 minutes of power-on; AssistiveTouch on;
   Settings → General → Keyboard → Hardware Keyboard → **U.S.** (the board sends U.S. key codes).
3. Board on the Mac's 2.4 GHz Wi-Fi: `drive.py net-sta <SSID>`, then reboot the board (replug it). AirPlay is only
   announced on that network after a reboot.

## Each session

```bash
D=hardware/tools/drive/drive.py
python3 $D info                      # ip + video token (new every boot)
python3 $D rec > /tmp/rec.log &      # keep the video session open
# iPhone: same Wi-Fi, close the ESP32 Agent app, Control Center → Screen Mirroring → ESP32 Agent
#         (start or restart it AFTER rec is running: a session only gets a picture from the next keyframe)
python3 $D shot                      # ~/.cache/esp32agent-drive/shot.png (+ shots.png, 300 px)
python3 $D tap 200 744               # pixels of the 720-wide frame
python3 $D type 'Hello'; python3 $D key enter
```

## Limits

- The AssistiveTouch pointer did not show in the mirror on an iPhone 12 with iOS 26, so taps are blind: about 1.32
  frame px per mouse count on both axes, from the top-left corner, x then y. Near the bottom it lands ~40 px high.
  Re-measure on another phone or after changing Pointer Sensitivity (`ESP32_SCALE_X`, `ESP32_SCALE_Y`).
- A shot decodes the whole recording since mirroring started; it gets slower over time. Restart mirroring (with
  `rec` running) to reset it. Fast animations can leave a smeared frame: take another shot.
- Only one viewer gets the video at a time; the iOS app and `rec` compete for it.
