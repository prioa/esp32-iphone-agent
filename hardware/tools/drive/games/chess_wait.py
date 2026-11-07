#!/usr/bin/env python3
"""Wait until the board differs from the last board.png, without anyone reading pictures: chess_wait.py [SECONDS]

Exit 0: changed, board.png is fresh, its path is printed. Exit 1: nothing changed within SECONDS (default 540).
"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chess_board as cb

POLL, SETTLE = 2, 1  # s between checks; s to let a piece animation finish before saving
MIN_DIFF = 8         # mean colour change of a square (0-255) that counts: above the pointer dot, below a piece
MIN_SQUARES = 2      # a move changes at least its from and to square


def means(path, vf):
    """Mean colour of each of the 64 squares, as 192 bytes."""
    return subprocess.run(["ffmpeg", "-loglevel", "quiet", "-i", path, "-vf", f"{vf}scale=8:8:flags=area",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout


def changed(a, b):
    return sum(max(abs(x - y) for x, y in zip(a[i:i + 3], b[i:i + 3])) >= MIN_DIFF for i in range(0, 192, 3))


last = means(cb.BOARD, "")  # compare with what was last saved, so a move made before this start still counts
end = time.time() + (float(sys.argv[1]) if len(sys.argv) > 1 else 540)
while time.time() < end:
    cb.drive.frame(cb.FULL)
    if changed(last, means(cb.FULL, cb.CROP + ",")) >= MIN_SQUARES:
        time.sleep(SETTLE)
        cb.save()
        print(cb.BOARD)
        sys.exit(0)
    time.sleep(POLL)
print("no change", file=sys.stderr)
sys.exit(1)
