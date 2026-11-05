#!/usr/bin/env python3
"""Drive the iPhone from a Mac: board on USB serial (commands), screen over the board's Wi-Fi video (TCP 7201).

For a board in station mode (`net sta`) on the Mac's Wi-Fi, mirroring the iPhone over that network. Taps are blind:
`home` slams the pointer into the top-left corner, then x and y move separately (diagonal moves accelerate more).
Coordinates are pixels of the 720-px-wide mirrored frame. See README.md.

  drive.py net-sta SSID      join a Wi-Fi (password from a hidden macOS dialog, sent only to the board)
  drive.py net-ap            back to the board's own hotspot
  drive.py info              save the board's ip + video token (new every boot) to the state dir
  drive.py rec               keep the video session open, append to rec.h264 (run in the background)
  drive.py shot [NAME]       newest frame -> NAME.png and a 300-px NAMEs.png in the state dir
  drive.py tap X Y           click at frame pixel X, Y
  drive.py drag X1 Y1 X2 Y2  press, move, release
  drive.py type TEXT         type ASCII (iPhone hardware keyboard layout must be U.S.)
  drive.py key COMBO         e.g. enter, cmd+a, backspace
  drive.py home-screen       the Home key
  drive.py cmd "LINE"        any board command, e.g. "status", "unpair"
"""
import glob, json, os, select, socket, struct, subprocess, sys, termios, threading, time

STATE = os.path.expanduser(os.environ.get("ESP32_DRIVE_DIR", "~/.cache/esp32agent-drive"))
SCALE_X = float(os.environ.get("ESP32_SCALE_X", "1.32"))  # frame px per mouse count (measured 2026-11-05, iPhone 12)
SCALE_Y = float(os.environ.get("ESP32_SCALE_Y", "1.32"))  # low near the bottom: aim ~40 px lower around y 1400
os.makedirs(STATE, exist_ok=True)


def serial_open():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no board on USB serial (/dev/cu.usbmodem*)")
    fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] |= termios.CLOCAL | termios.CREAD
    a[4] = a[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def board(*lines, wait=1.0):
    """Send `<id> <command>` lines over USB serial; return {id: reply}. Waits `wait` s after each line."""
    fd, out = serial_open(), b""
    for i, line in enumerate(lines):
        os.write(fd, f"{900 + i} {line}\n".encode())
        end = time.time() + wait
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out += os.read(fd, 4096)
                except OSError:
                    break
    os.close(fd)
    replies = {}
    for l in out.decode("utf8", "replace").splitlines():
        head, _, rest = l.partition(" ")
        if head.isdigit():
            replies[int(head)] = rest
    return replies


def state(name, value=None):
    path = os.path.join(STATE, name)
    if value is None:
        return open(path).read().strip()
    open(path, "w").write(value)


def info():
    fd = serial_open()
    os.write(fd, b"0 netinfo\n")
    out, end = b"", time.time() + 1.5
    while time.time() < end:
        if select.select([fd], [], [], 0.05)[0]:
            out += os.read(fd, 4096)
    os.close(fd)
    for l in out.decode("utf8", "replace").splitlines():
        if l.startswith("0 {"):
            d = json.loads(l[2:])
            state("ip", d["ip"])
            state("vtok", d["vtok"])
            print("mode", d.get("mode"), "ip", d["ip"], "(video token saved)")
            return
    sys.exit("no netinfo answer")


def rec():
    """One long video session: a new one starts at the last IDR, so start it before Screen Mirroring (re)starts."""
    ip, vtok = state("ip"), state("vtok")
    while True:
        try:
            s = socket.create_connection((ip, 7201), timeout=10)
            s.sendall(f"hello {vtok}\n".encode())
            s.settimeout(10)
            out = open(os.path.join(STATE, "rec.h264"), "wb")

            def keepalive():
                while True:
                    time.sleep(1)
                    try:
                        s.sendall(b"ka\n")
                    except OSError:
                        return
            threading.Thread(target=keepalive, daemon=True).start()
            buf, total, last = b"", 0, time.time()
            while True:
                d = s.recv(65536)
                if not d:
                    raise OSError("closed")
                buf += d
                while len(buf) >= 2:
                    n = struct.unpack_from("<H", buf)[0]
                    if len(buf) < 2 + n:
                        break
                    rec_, buf = buf[2:2 + n], buf[2 + n:]
                    if n < 3 or rec_[2] & 16:  # keepalive answer / bench filler
                        continue
                    p = rec_[3:]
                    if rec_[2] & 1:            # START chunk: u32 board timestamp first
                        p = p[4:]
                    out.write(p)
                    out.flush()
                    total += len(p)
                if time.time() - last > 30:
                    print(time.strftime("%H:%M:%S"), total, "bytes", flush=True)
                    last = time.time()
        except OSError as e:
            print(time.strftime("%H:%M:%S"), "reconnect:", e, flush=True)
            time.sleep(2)


def shot(name="shot"):
    src, cur = os.path.join(STATE, "rec.h264"), os.path.join(STATE, "cur.h264")
    subprocess.run(["cp", src, cur], check=True)
    png, small = os.path.join(STATE, name + ".png"), os.path.join(STATE, name + "s.png")
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-f", "h264", "-i", cur, "-update", "1", "-y", png])
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", png, "-vf", "scale=300:-1", small])
    print(png, small)


def goto(x, y):
    return ["home", f"move {round(x / SCALE_X)} 0", f"move 0 {round(y / SCALE_Y)}"]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c, a = sys.argv[1], sys.argv[2:]
    if c == "info":
        info()
    elif c == "rec":
        rec()
    elif c == "shot":
        shot(*a[:1])
    elif c == "tap":
        print(board(*goto(float(a[0]), float(a[1])), "click", wait=0.9))
    elif c == "drag":
        print(board(*goto(float(a[0]), float(a[1])), "press",
                    f"move {round((float(a[2]) - float(a[0])) / SCALE_X)} {round((float(a[3]) - float(a[1])) / SCALE_Y)}",
                    "release", wait=0.9))
    elif c == "type":
        print(board("type " + " ".join(a), wait=2))
    elif c == "key":
        print(board("key " + a[0]))
    elif c == "home-screen":
        print(board("homescreen"))
    elif c == "cmd":
        line = " ".join(a)
        if line in ("status", "netinfo"):  # serial-only forms: `0 status`, `0 netinfo`
            fd = serial_open()
            os.write(fd, f"0 {line}\n".encode())
            out, end = b"", time.time() + 1.5
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    out += os.read(fd, 4096)
            os.close(fd)
            print("\n".join(l[2:] for l in out.decode("utf8", "replace").splitlines() if l.startswith("0 {")))
        else:
            print(board(line, wait=1.5))
    elif c == "net-ap":
        fd = serial_open()
        os.write(fd, b'0 net {"mode":"ap"}\n')
        os.close(fd)
        print("sent; the board opens its hotspot again")
    elif c == "net-sta":
        ssid = a[0]
        r = subprocess.run(["osascript", "-e", f'text returned of (display dialog "Wi-Fi password for {ssid} '
                            f'(goes to the ESP32 board only)" default answer "" with hidden answer with title '
                            f'"ESP32 Agent")'], capture_output=True, text=True)
        if r.returncode:
            sys.exit("cancelled")
        fd = serial_open()
        os.write(fd, ("0 net " + json.dumps({"mode": "sta", "ssid": ssid, "pass": r.stdout.rstrip("\n")}) + "\n").encode())
        os.close(fd)
        print("sent; reboot the board afterwards so AirPlay is announced on that network")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
