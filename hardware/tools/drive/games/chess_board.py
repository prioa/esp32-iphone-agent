#!/usr/bin/env python3
"""Save only the board of the Chess.com app to board.png in the state dir and print its path: chess_board.py

Board geometry is in chess.md.
"""
import os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import drive

SIZE, TOP = 720, 457  # frame px: the board fills the width, y 457 to 1177
CROP = f"crop={SIZE}:{SIZE}:0:{TOP}"
FULL, BOARD = os.path.join(drive.STATE, "board_full.png"), os.path.join(drive.STATE, "board.png")


def save():
    drive.frame(FULL)
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", FULL, "-vf", CROP, BOARD], check=True)


if __name__ == "__main__":
    save()
    print(BOARD)
