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

1. Flash the test build, Bluetooth even with USB plugged in: `hardware/flash.sh --debug --ble-hid --drop-sent`, then
   `drive.py cmd "fps 2"` (stored on the board, read when mirroring starts). See Limits.
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

## Web page

```bash
python3 -I hardware/tools/drive/webui.py             # http://127.0.0.1:8770, this Mac only
```

It shows the connection chain, the session steps with the next one marked, the live picture and every command with the
board lines it sent. The picture shows each frame as it arrives, so its rate is the board's (see Limits). Click the picture to tap, drag it to drag; text, keys and board commands have their own fields. The
log is `webui_log.jsonl` in the state dir. The Wi-Fi password and the video token never reach the page or the log, so
`net` is refused there: use `drive.py net-sta`.

AI tasks ("open Settings and tell me the iOS version") run as `claude -p` on your Claude Code login, no API key, and count
towards that account's limits. Screenshots go to Anthropic. The run has no built-in tools and no settings, only the phone
tools from `webui_mcp.py`, which call back into the page's queue and checks; a task runs until it ends or you stop it, there is no step limit. The model is
told to ask before it sends, buys, deletes or changes an account, but that is a rule for the model, not a lock: a tap on
"Send" looks like any other tap. Tick **Jeden Schritt bestätigen** to hold every action until you approve it.

`drive.py` now takes a lock around the serial port, because two users steal each other's replies. A script started
before that change does not take it: while one runs the page does not touch the port and refuses commands, so restart
it. Commands are also refused while the Eatventure bot works, unless you tick the override.

Tests, without a board: `python3 -I hardware/tools/drive/test_webui.py`.

## Limits

- The AssistiveTouch pointer did not show in the mirror on an iPhone 12 with iOS 26, so taps are blind: about 1.32
  frame px per mouse count on both axes, from the top-left corner, x then y. Near the bottom it lands ~40 px high.
  Re-measure on another phone or after changing Pointer Sensitivity (`ESP32_SCALE_X`, `ESP32_SCALE_Y`).
- Screen Mirroring sends one keyframe at the start and only changes after it. `rec` therefore runs an ffmpeg that
  decodes the stream as it arrives and keeps the newest frame in `live.png`, so a shot is instant. Without it (an
  older `rec`), a shot decodes the whole recording since mirroring started and gets slower over time (~27 s after 20
  minutes). Fast animations can leave a smeared frame: take another shot.
- A busy game screen costs ~40 KB per frame. At the default 5 fps the board took in more than it got out to the Mac
  (120-160 vs ~90 KB/s), fell behind and froze until mirroring restarted. `fps 2` gives about 1 frame/s, ~36 KB/s.
  `--drop-sent` frees video already sent, so the buffer does not fill, but a viewer that joins later (a restarted
  `rec`, the app) gets a picture only after mirroring restarts.
- A tap goes from the pointer's last position (0.4-1.3 s) and from the corner every 10 moves or after 2 idle minutes
  (2-3.3 s). `drag`, `flick` and `cmd` forget the position. If taps start to miss, lower `ESP32_HOME_EVERY`. A tap
  shows in the picture after 1.5 to 3 s, sometimes 8 s or more.
- Games can ignore a long `drag`: use `flick`, see `games/`.
- Run only one `rec`: a second one truncates `rec.h264` under the first.
- Only one viewer gets the video at a time; the iOS app and `rec` compete for it.
