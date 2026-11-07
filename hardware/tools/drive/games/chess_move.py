#!/usr/bin/env python3
"""Play a move in the Chess.com app: chess_move.py e2e4, or chess_move.py e7e5 black with Black at the bottom

Taps the piece, then the target. Board geometry and pointer correction are in chess.md.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import drive

SQUARE, TOP = 90, 457  # frame px: the board fills the width; rank 8 starts at y 457 in a game against a human (503 against a bot)


def square(name, black=False):
    file, rank = ord(name[0]) - ord("a"), int(name[1])
    col, row = (7 - file, rank - 1) if black else (file, 8 - rank)
    x = (col + 0.5) * SQUARE
    y = TOP + (row + 0.5) * SQUARE
    # the pointer lands at about 0.96 * tap + 22 (x), + 25 (y), measured from the visible pointer 2026-11-05
    return (x - 22) / 0.96, (y - 25) / 0.96


move, black = sys.argv[1], sys.argv[2:] == ["black"]
for name in (move[:2], move[2:4]):
    print(name, drive.board(*drive.goto(*square(name, black)), "click"))
