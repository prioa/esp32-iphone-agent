#!/usr/bin/env python3
"""Upgrade an open Eatventure station: click the button for 5 s, new shot, go on only while the price button is still blue.

usage: python3 -I eatventure_up.py X Y [ROUNDS]    X Y: centre of the blue price button, in frame pixels

Stops when the button turned grey (too expensive) or something covers it (a popup). See eatventure.md.
"""
import importlib
import os
import subprocess
import sys
import time

CLICKS = 14  # about 5 s; holding the button with the mouse buys only one level (2026-11-05)
LAG = 3  # a tap usually shows in the picture after 1.5 to 3 s


def button_rgb(drive, x, y):
    """Mean colour of the button's left end, away from the price text and the pointer."""
    drive.shot("up")
    r = subprocess.run(["ffmpeg", "-loglevel", "quiet", "-i", os.path.join(drive.STATE, "up.png"), "-vf",
                        f"crop=24:24:{x - 115}:{y - 20},scale=1:1,format=rgb24", "-f", "rawvideo", "-"],
                       capture_output=True)
    return tuple(r.stdout[:3])


def main():
    x, y = int(sys.argv[1]), int(sys.argv[2])
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    drive = importlib.import_module("drive")
    drive.board(*drive.goto(x, y))
    for i in range(rounds):
        rgb = button_rgb(drive, x, y)
        if not (len(rgb) == 3 and rgb[2] > 200 and rgb[0] < 140):
            print(f"stop before round {i + 1}: button colour {rgb}")
            return
        drive.board(*["click"] * CLICKS, wait=0.24)  # + ~60 ms per click: same pace as before
        time.sleep(LAG)
    print(f"all {rounds} rounds done")


if __name__ == "__main__":
    main()
