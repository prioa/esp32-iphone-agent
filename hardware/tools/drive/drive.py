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
  drive.py flick X Y DY [N]  N quick flicks of DY mouse counts: DY < 0 scrolls down the page, > 0 up (try 120)
  drive.py type TEXT         type ASCII (iPhone hardware keyboard layout must be U.S.)
  drive.py key COMBO         e.g. enter, cmd+a, backspace
  drive.py home-screen       the Home key
  drive.py cmd "LINE"        any board command, e.g. "status", "unpair"
"""
import contextlib, fcntl, glob, json, os, select, socket, struct, subprocess, sys, termios, threading, time

STATE = os.path.expanduser(os.environ.get("ESP32_DRIVE_DIR", "~/.cache/esp32agent-drive"))
SCALE_X = float(os.environ.get("ESP32_SCALE_X", "1.32"))  # frame px per mouse count (measured 2026-11-05, iPhone 12)
SCALE_Y = float(os.environ.get("ESP32_SCALE_Y", "1.32"))  # low near the bottom: aim ~40 px lower around y 1400
AUD = b"\x00\x00\x00\x01\x09\x10"  # H.264 access unit delimiter
HOME_EVERY = int(os.environ.get("ESP32_HOME_EVERY", "10"))  # moves between two trips to the corner
HOME_IDLE = float(os.environ.get("ESP32_HOME_IDLE", "120"))  # s without a move: someone else may have moved it
os.makedirs(STATE, exist_ok=True)
trace = None  # set to fn(lines, replies) to see what every board() call sent and got back (webui.py)


class PortBusy(Exception):
    """Another call or process has the serial port, and the caller asked not to wait."""


@contextlib.contextmanager
def port_lock(block=True):
    """One user of the serial port at a time, across threads and processes: two readers steal each other's replies."""
    fd = os.open(os.path.join(STATE, "serial.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            raise PortBusy() from None
        yield
    finally:
        os.close(fd)  # also lets go of the lock


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


def board(*lines, wait=0.03, timeout=20):
    """Send `<id> <command>` lines over USB serial; return {id: reply}.

    The board replies once a command has run, so each line waits for its reply (at most `timeout` s: a 512-character
    `type` takes ~15 s), then `wait` s more to let the phone catch up.
    """
    with port_lock():
        fd, out, replies = serial_open(), b"", {}
        for i, line in enumerate(lines):
            id_ = 900 + i
            os.write(fd, f"{id_} {line}\n".encode())
            end = time.time() + timeout
            while id_ not in replies and time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        out += os.read(fd, 4096)
                    except OSError:
                        break
                    *done, out = out.split(b"\n")
                    for l in done:
                        head, _, rest = l.decode("utf8", "replace").strip().partition(" ")
                        if head.isdigit():
                            replies[int(head)] = rest
            time.sleep(wait[i] if isinstance(wait, (list, tuple)) else wait)  # wait may be one value per line
        os.close(fd)
    if len(replies) < len(lines) or any(r.startswith("err") for r in replies.values()):
        forget_pointer()
    if trace:
        trace(lines, replies)
    return replies


def query(line, timeout=1.5, block=True):
    """Ask a serial-only JSON command (`status`, `netinfo`): the parsed answer as soon as it comes, else None.

    block=False raises PortBusy instead of waiting for the port.
    """
    with port_lock(block):
        fd, out, end = serial_open(), b"", time.time() + timeout
        try:
            os.write(fd, f"0 {line}\n".encode())
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    out += os.read(fd, 4096)
                    *done, out = out.split(b"\n")
                    for l in done:
                        text = l.decode("utf8", "replace").strip()
                        if text.startswith("0 {"):
                            return json.loads(text[2:])
        except (OSError, ValueError):
            pass
        finally:
            os.close(fd)
    return None


def state(name, value=None):
    path = os.path.join(STATE, name)
    if value is None:
        return open(path).read().strip()
    open(path, "w").write(value)


def info():
    d = query("netinfo")
    if not d:
        sys.exit("no netinfo answer")
    state("ip", d["ip"])
    state("vtok", d["vtok"])
    print("mode", d.get("mode"), "ip", d["ip"], "(video token saved)")


def live_decoder():
    """ffmpeg that decodes the stream as it arrives and keeps the newest frame in live.png (atomic rename).

    Screen Mirroring sends one IDR at the start and only P-frames after it, so decoding rec.h264 from the top for
    every shot gets slower the longer mirroring runs (about 27 s after 20 minutes). This keeps a shot instant.
    """
    try:
        return subprocess.Popen(
            # small probe: ffmpeg otherwise buffers ~5 MB before the first frame. One thread on both sides: PNG's
            # frame threads hold back about 6 frames, 12 s at 0.5 fps (fine up to ~20 fps, measured 2026-11-06).
            ["ffmpeg", "-loglevel", "quiet", "-probesize", "32768", "-analyzeduration", "0", "-threads", "1",
             "-f", "h264", "-i", "pipe:0", "-threads", "1", "-f", "image2", "-update", "1", "-atomic_writing", "1", "-y",
             os.path.join(STATE, "live.png")], stdin=subprocess.PIPE)
    except OSError:
        return None


def rec():
    """One long video session: a new one starts at the last IDR, so start it before Screen Mirroring (re)starts."""
    ip, vtok = state("ip"), state("vtok")
    dec = None
    while True:
        try:
            s = socket.create_connection((ip, 7201), timeout=10)
            s.sendall(f"hello {vtok}\n".encode())
            s.settimeout(10)
            out = open(os.path.join(STATE, "rec.h264"), "wb")
            if dec:
                dec.kill()
            dec = live_decoder()

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
                    if dec:
                        try:
                            dec.stdin.write(p)
                            if rec_[2] & 2 and not rec_[2] & 8:  # END of a frame: an AUD makes the parser let go of
                                dec.stdin.write(AUD)             # it now, not when the next frame starts
                            dec.stdin.flush()
                        except OSError:
                            dec = None
                    total += len(p)
                if time.time() - last > 30:
                    print(time.strftime("%H:%M:%S"), total, "bytes", flush=True)
                    last = time.time()
        except OSError as e:
            print(time.strftime("%H:%M:%S"), "reconnect:", e, flush=True)
            time.sleep(2)


def frame(png):
    """Save the newest frame to the path `png`."""
    live = os.path.join(STATE, "live.png")
    if os.path.exists(live) and os.path.getmtime(live) >= os.path.getmtime(os.path.join(STATE, "rec.h264")) - 5:
        subprocess.run(["cp", live, png], check=True)  # rec's live decoder; static screens send no new frames
    else:  # no live decoder (older rec): decode the whole recording since the last IDR
        src, cur = os.path.join(STATE, "rec.h264"), os.path.join(STATE, "cur.h264")
        subprocess.run(["cp", src, cur], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "quiet", "-f", "h264", "-i", cur, "-update", "1", "-y", png])


def shot(name="shot"):
    png, small = os.path.join(STATE, name + ".png"), os.path.join(STATE, name + "s.png")
    frame(png)
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", png, "-vf", "scale=300:-1", small])
    print(png, small)


def forget_pointer():
    """The next goto starts from the corner: the pointer may not be where we think."""
    try:
        os.remove(os.path.join(STATE, "pointer"))
    except OSError:
        pass


def goto(x, y):
    """Board lines that bring the pointer to frame pixel x, y: from where it is, from the corner now and then."""
    tx, ty = round(x / SCALE_X), round(y / SCALE_Y)
    try:
        p = json.loads(state("pointer"))
        fresh = p["n"] < HOME_EVERY and time.time() - p["t"] < HOME_IDLE
    except (OSError, ValueError, KeyError):
        fresh = False
    lines, n = ([], p["n"] + 1) if fresh else (["home"], 0)
    dx, dy = (tx - p["x"], ty - p["y"]) if fresh else (tx, ty)
    if dx:
        lines.append(f"move {dx} 0")
    if dy:
        lines.append(f"move 0 {dy}")
    state("pointer", json.dumps({"x": tx, "y": ty, "n": n, "t": time.time()}))
    return lines


def tap(x, y):
    return board(*goto(x, y), "click")


def drag(x1, y1, x2, y2):
    r = board(*goto(x1, y1), "press", f"move {round((x2 - x1) / SCALE_X)} {round((y2 - y1) / SCALE_Y)}", "release")
    forget_pointer()  # a diagonal move accelerates: its distance is not known
    return r


def flick(x, y, dy, n=1):
    board(*goto(x, y))
    for _ in range(n):
        board("press", f"move 0 {dy}", "release", wait=0.02)
        time.sleep(0.4)
        board(f"move 0 {-dy}", wait=0.02)
        time.sleep(0.4)
    forget_pointer()


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
        print(tap(float(a[0]), float(a[1])))
    elif c == "drag":
        print(drag(float(a[0]), float(a[1]), float(a[2]), float(a[3])))
    elif c == "flick":
        # press, a short fast move, release at once, N times. A game's view (Eatventure) follows only the very start
        # of a long drag and snaps back; a flick keeps its way and its momentum. Measured 2026-11-05.
        flick(float(a[0]), float(a[1]), int(a[2]), int(a[3]) if len(a) > 3 else 1)
    elif c == "type":
        print(board("type " + " ".join(a)))
    elif c == "key":
        print(board("key " + a[0]))
    elif c == "home-screen":
        print(board("homescreen"))
    elif c == "cmd":
        line = " ".join(a)
        if line in ("status", "netinfo"):  # serial-only forms: `0 status`, `0 netinfo`
            d = query(line)
            print(json.dumps(d) if d else "")
        else:
            forget_pointer()
            print(board(line))
    elif c == "net-ap":
        with port_lock():
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
        with port_lock():
            fd = serial_open()
            os.write(fd, ("0 net " + json.dumps({"mode": "sta", "ssid": ssid, "pass": r.stdout.rstrip("\n")}) + "\n").encode())
            os.close(fd)
        print("sent; reboot the board afterwards so AirPlay is announced on that network")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
