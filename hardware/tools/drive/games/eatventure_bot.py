#!/usr/bin/env python3
"""Play the routine of Eatventure without a model in the loop: build/fly, crates, upgrades menu, station rounds, sweeps.

usage: python3 -I eatventure_bot.py [--minutes N] [--clicks N]    play (see eatventure.md for the rules it follows)
       python3 -I eatventure_bot.py --check PNG...                only print what it sees, tap nothing

Exit code 0: time is up. Exit code 2: it stopped on a screen it does not know and saved it as bot_stop.png in the
state dir; look at that and decide. Restaurants differ, so it knows no station positions or food pictures: arrows,
panels, buttons and crates are found by shape and colour, only the game's fixed bars and icons have coordinates.
"""
import argparse
import difflib
import json
import os
import random
import re
import subprocess
import threading
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drive

# Fixed parts of the game's UI and the AssistiveTouch button (x0, y0, x1, y1): never a tap target. Tapping them opens
# shops, events or the iOS menu. Move the AssistiveTouch button and this list has to follow.
AT_LEFT = 590
NO_TAP = [(0, 0, 720, 172), (15, 130, 95, 210), (0, 230, 95, 330), (0, 350, 95, 450), (0, 1235, 95, 1335),
          (628, 230, 720, 330), (628, 350, 720, 450), (625, 475, 720, 570), (628, 590, 720, 690),
          (0, 1360, 720, 1558)]
BAR_BUTTONS = [67, 180, 360, 540]  # x of the bottom bar's blue buttons, at y 1430 (a new restaurant has no upgrades arrow yet)
UPGRADES = (652, 1445)
BADGE = (585, 1335, 645, 1395)  # the red arrow on it: something in the menu is affordable
NEXT = (67, 1430)  # the button at the bottom left: a build icon, or the plane as the last stage of a restaurant
NEXT_BADGE = (65, 1335, 130, 1395)  # the red arrow on it: it is active, and using it is the goal
TRUCK = (45, 400)  # the food delivery icon at the left edge: with the helper icon, the only overlays the bot may open
TRUCK_BADGE = (60, 335, 108, 380)  # its red "!": there is an order to pick (about 700 red pixels, none without it)
TRUCK_X = (600, 322)  # the close button of its dialog
TRUCK_RETRY = 300.0  # how long to leave it alone after its dialog held nothing to accept
HELPER = (45, 280)  # the person icon above the truck: invite a helper
HELPER_BADGE = (60, 215, 108, 260)  # its red "!"
HELPER_MIN = 4  # a helper's item below this level does not count (user's rule)
MENU_X = (612, 510)
MENU_ROWS = [625, 745, 865, 985]
MENU_BUY_X = 600  # right part of the price buttons: the pointer shows in the picture and must not hide the coin at x 510-545
MENU_COIN = (495, 555)  # the part of the top row's button that tells blue with a coin from grey, or gems, or an ad
ICON_OFFSET = 28  # a station's food icon sits this far right of and below its arrow
SETTLE = 3.0  # a tap usually shows in the picture after 1.5 to 3 s
WALK_SCROLL = os.environ.get("EATVENTURE_SCROLL", "alt2")  # alt1 twice: one alt1 scrolls very little  # a name from SCROLLS: what the walk through the restaurant uses
SCROLL_FLICKS = 2  # about 500 px: the free strip right of the fixed icons is 650 px tall (y 690-1340), so every spot passes it
FLICK_SHOW = 4.0  # after a flick, before comparing the picture
SLOT_RETRY = 120.0  # how long to leave an empty slot without an arrow alone that was not affordable
WATCH = 3.0  # how often to look at the screen while there is nothing to do
COOL = 25.0  # a station that had its turn is left alone this long (its arrow comes back once money does)
STALL = 150.0  # checked twice in a row: about 5 minutes without an action or a change of money means stuck
SHOW = 12.0  # but it can take 8 s or more: how long to look for what a tap should open


CRATE = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eatventure_crate.png"), cv2.IMREAD_GRAYSCALE)
CRATE_MATCH = 0.72  # real crates 0.88 and up, half hidden ones too; nothing else above 0.56 (2026-11-06)


def blocked(x, y):
    return any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in NO_TAP)


def mask(img, lo, hi):
    return cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), np.array(lo), np.array(hi))


def blue_mask(img):
    return mask(img, (95, 120, 200), (112, 255, 255))


def components(m):
    """(x, y, w, h, area) of each connected blob."""
    return [tuple(int(v) for v in s) for s in cv2.connectedComponentsWithStats(m, connectivity=8)[2][1:]]


def holds_arrow(img, x, y, w, h):
    """A white up arrow inside this red circle? A ! is two blobs, and too narrow."""
    white = cv2.inRange(img[y:y + h, x:x + w], (225, 225, 225), (255, 255, 255))
    ring = np.zeros_like(white)  # only inside the circle: the corners show what lies behind it
    cv2.circle(ring, (w // 2, h // 2), int(min(w, h) * 0.42), 255, -1)
    white = cv2.bitwise_and(white, ring)
    pts = cv2.findNonZero(white)
    if pts is None or sum(c[4] > 8 for c in components(white)) != 1:
        return False
    _, _, ww, wh = cv2.boundingRect(pts)
    return wh > 0 and ww / wh >= 0.6 and wh >= h * 0.35


ARROW = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eatventure_arrow.png"))  # a clean one, 38x40
ARROW_MASK = np.zeros(ARROW.shape, np.uint8)  # only the circle: its corners show what lies behind it
cv2.circle(ARROW_MASK, (19, 20), 18, (255, 255, 255), -1)
ARROW_MATCH = 0.8


def merged_arrows(img, x, y, w, h):
    """Arrows in a red blob that is no circle, because the arrow touches something red behind it (the left station's
    table, 2026-11-06). Found by their look."""
    x0, y0 = max(x - 4, 0), max(y - 4, 0)
    part = img[y0:y + h + 4, x0:x + w + 4]
    if part.shape[0] < ARROW.shape[0] or part.shape[1] < ARROW.shape[1]:
        return []
    res = np.nan_to_num(cv2.matchTemplate(part, ARROW, cv2.TM_CCOEFF_NORMED, mask=ARROW_MASK), posinf=0, neginf=0)
    found = []
    while True:
        _, score, _, (px, py) = cv2.minMaxLoc(res)
        if score < ARROW_MATCH:
            return found
        if holds_arrow(img, x0 + px, y0 + py, ARROW.shape[1], ARROW.shape[0]):
            found.append((x0 + px + 19, y0 + py + 20))
        res[max(py - 30, 0):py + 30, max(px - 30, 0):px + 30] = 0


def red_arrows(img):
    """Centres of red circles holding a white up arrow. Two overlapping circles (an arrow beside the ! of an icon) are
    one wide blob: then each end is looked at as a circle of its own."""
    red = cv2.bitwise_or(mask(img, (0, 130, 170), (7, 255, 255)), mask(img, (173, 130, 170), (180, 255, 255)))
    found = []
    for x, y, w, h, area in components(red):
        if area < 450:
            continue
        if 30 <= h <= 62 and 30 <= w <= 58 and abs(w - h) <= 8:
            if holds_arrow(img, x, y, w, h):
                found.append((x + w // 2, y + h // 2))
            continue
        here = []
        if 30 <= h <= 62 and 58 < w <= 2 * h:
            here = [(cx + h // 2, y + h // 2) for cx in (x, x + w - h) if holds_arrow(img, cx, y, h, h)]
        found += here or merged_arrows(img, x, y, w, h)
    return found


def stations(img):
    """Tap points (icon centres) of the arrows in the restaurant that are safe to tap."""
    out = []
    for x, y in red_arrows(img):
        if blocked(x, y):
            continue
        # the icon first; if a fixed icon lies over it, a free part of the same station or slot
        for dx, dy in ((ICON_OFFSET, ICON_OFFSET), (10, 46), (6, 36), (ICON_OFFSET, 60)):
            if not blocked(x + dx, y + dy):
                out.append((x + dx, y + dy))
                break
    return out


def badge(img):
    x0, y0, x1, y1 = BADGE
    return any(x0 <= x < x1 and y0 <= y < y1 for x, y in red_arrows(img))


def next_active(img):
    x0, y0, x1, y1 = NEXT_BADGE
    return any(x0 <= x < x1 and y0 <= y < y1 for x, y in red_arrows(img))


def icon_ready(img, box):
    x0, y0, x1, y1 = box
    a = img[y0:y1, x0:x1]
    return np.count_nonzero(cv2.bitwise_or(mask(a, (0, 130, 170), (7, 255, 255)), mask(a, (173, 130, 170), (180, 255, 255)))) >= 600


DIGITS = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eatventure_digits.png"), cv2.IMREAD_GRAYSCALE)
DIGIT_MATCH = 0.75  # real digits 0.79 and up, other digits at most 0.71 (2026-11-06)


def item_level(c):
    """The white number in the corner of a helper's item, None if a digit is not known."""
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    found = []
    for x, y, w, h, _ in sorted(components((g > 205).astype(np.uint8) * 255)):
        if 11 <= h <= 22 and w <= 18 and x and y and x + w < c.shape[1] and y + h < c.shape[0]:
            q = cv2.resize(g[y:y + h, x:x + w], (20, 30), interpolation=cv2.INTER_AREA)
            scores = [(float(cv2.matchTemplate(q, DIGITS[:, d * 20:d * 20 + 20], cv2.TM_CCOEFF_NORMED)[0, 0]), d) for d in range(10) if DIGITS[:, d * 20:d * 20 + 20].any()]
            if max(scores)[0] < DIGIT_MATCH:
                return None
            found.append(str(max(scores)[1]))
    return int("".join(found)) if found else None


def helper_items(img, cy):
    """[(red?, level)] of the six item slots of a helper's row. The frame's colour tells a red item, none: an empty slot."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    ring = np.ones((50, 50), bool)
    ring[4:46, 4:46] = False
    items = []
    for k in range(6):
        cx = 99 + 56 * k
        px = hsv[cy - 25:cy + 25, cx - 25:cx + 25][ring]
        col = px[px[:, 1] > 90]
        if len(col) >= 80:
            h = int(np.median(col[:, 0]))
            items.append((h <= 5 or h >= 175, item_level(img[cy - 25:cy + 1, cx - 25:cx + 2])))
    return items


def helper_score(items):
    """More red items first, then higher levels. Items below HELPER_MIN and unread levels do not count."""
    good = [(r, l) for r, l in items if l is not None and l >= HELPER_MIN]
    return sum(r for r, _ in good), sum(l for _, l in good)


def dialog_button(img):
    """Centre of the main button of a dialog over the dimmed game, if it holds white text and at most a gold coin (game
    money: "Renovieren"). A gem or any other symbol on it: None, like for any other dialog."""
    bar = all(np.count_nonzero(blue_mask(img[1420:1440, x - 10:x + 10])) > 40 for x in BAR_BUTTONS)
    if bar:
        return None
    area = img.copy()
    area[:300] = 0
    area[1300:] = 0
    blue = cv2.morphologyEx(blue_mask(area), cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    for x, y, w, h, _ in components(blue):
        if 220 <= w <= 420 and 60 <= h <= 110:
            b = cv2.cvtColor(img[y + 6:y + h - 6, x + 6:x + w - 6], cv2.COLOR_BGR2HSV)
            colour = (b[..., 1] > 80) & (((b[..., 0] < 90) & ~((b[..., 0] >= 8) & (b[..., 0] <= 32))) | (b[..., 0] > 125))
            return (x + w // 2, y + h // 2) if np.count_nonzero(colour) < 40 else None
    return None


def dialog_x(img):
    """Centre of a dialog's red close button at its top right."""
    area = img[380:560, 520:680]
    red = cv2.bitwise_or(mask(area, (0, 130, 170), (7, 255, 255)), mask(area, (173, 130, 170), (180, 255, 255)))
    for x, y, w, h, a in components(red):
        if 50 <= w <= 100 and 50 <= h <= 100:
            return 520 + x + w // 2, 380 + y + h // 2
    return None


def panel_button(img):
    """The big button of an open station panel: (kind, cx, cy, gold pixels, box), kind 'blue' or 'grey'.

    Found by the button, not the white panel, which merges with white things behind it and may be cut off by the top
    bar. cx is clear of the AssistiveTouch button. box is the part of the screen the panel may cover.
    """
    area = img.copy()
    area[:172] = 0
    area[1360:] = 0
    white = cv2.inRange(area, (240, 240, 240), (255, 255, 255))
    # open first: thin blue things behind the panel (a parasol's stripes) must not merge with the button
    blue = cv2.morphologyEx(blue_mask(area), cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    grey = cv2.morphologyEx(mask(area, (0, 0, 185), (179, 10, 222)), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    for kind, m in (("blue", blue), ("grey", grey)):
        for x, y, w, h, _ in components(m):
            if not (150 <= w <= 290 and 55 <= h <= 110 and y >= 186):
                continue
            above = white[y - 12, x:x + w]  # a panel's white lies right above the button
            if np.count_nonzero(above) < 0.7 * w:
                continue
            right = min(x + w, AT_LEFT - 5)
            gold = int(np.count_nonzero(mask(area[y:y + h, x:x + w], (14, 120, 180), (28, 255, 255))))
            box = (x - 16, max(y - 215, 172), 290, min(h + 240, 340))
            return kind, (x + right) // 2, y + h // 2, gold, box
    return None


def button_price(img, b):
    """The price on a station's button as text ("1.21B"), None if unreadable: white on blue, red on grey. The pointer must
    not lie on it."""
    x0, y0 = max(b[1] - 150, 0), b[2] - 32
    c = img[y0:y0 + 64, x0:x0 + 300]
    ink = cv2.inRange(c, (232, 232, 232), (255, 255, 255)) if b[0] == "blue" else \
        cv2.bitwise_or(mask(c, (0, 110, 120), (7, 255, 255)), mask(c, (173, 110, 120), (180, 255, 255)))
    big = cv2.resize(255 - ink, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    big = cv2.copyMakeBorder(big, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    big = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
    for line in ocr(big, (0, 0, big.shape[1], big.shape[0])):
        m = re.search(r"\d+(?:[.,]\d+)?\s?[A-Za-z]{0,2}", line[4])
        if m:
            return m.group(0).replace(",", ".").replace(" ", "")
    return None


def cost_key(text):
    """A price as something to sort by: K, M, B, T, then aa, ab, ... each a thousand times the last. Unknown comes first,
    so that it gets read."""
    m = re.fullmatch(r"([\d.]+)([A-Za-z]{0,2})", text or "")
    if not m:
        return (-1, 0.0)
    unit = m.group(2)
    tiers = {"": 0, "K": 1, "M": 2, "B": 3, "T": 4}
    if unit in tiers:
        tier = tiers[unit]
    elif len(unit) == 2 and unit.islower():
        tier = 5 + (ord(unit[0]) - 97) * 26 + ord(unit[1]) - 97
    else:
        return (-1, 0.0)
    try:
        return (tier, float(m.group(1)))
    except ValueError:
        return (-1, 0.0)


def is_unlock(img, b):
    """Is this button's panel "Freischalten"? By its text: the panel's shape took finished stations for free slots, which
    then got one click instead of everything affordable (2026-11-06). A panel it misreads only gets treated as a station."""
    return any("Freischalten" in l[4] for l in ocr(img, b[4]))


def panel(img):
    """Where an open station panel may be, or None."""
    b = panel_button(img)
    return b[4] if b else None


def crates(img):
    """Centres of wooden crates, by comparing their slats with eatventure_crate.png at a few sizes. Only the slats: the
    lower half can be hidden behind a machine. Colours alone fail: a crate merges with parquet, chairs look like one."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    found = []
    for sx, sy in ((1, 1), (0.95, 1.05), (1, 1.05), (0.95, 1), (1.05, 1.05), (1.05, 1), (0.9, 0.95), (1.1, 1.1)):
        t = cv2.resize(CRATE[8:60, 6:-6], None, fx=sx, fy=sy)
        h, w = t.shape
        res = cv2.matchTemplate(grey[172:1360], t, cv2.TM_CCOEFF_NORMED)
        for y, x in zip(*np.where(res > CRATE_MATCH)):
            cx, cy = int(x) + w // 2, int(y) + 172 + h // 2
            if not blocked(cx, cy) and all(abs(cx - a) > 40 or abs(cy - b) > 40 for a, b in found):
                found.append((cx, cy))
    return found


def slots(img):
    """Centres of empty station slots: rectangles of white dashes, maybe cut off by the bottom bar. Newspapers and the
    like also make small white blobs, but those do not lie along the edge of a box."""
    white = cv2.inRange(img, (222, 222, 222), (255, 255, 255))
    white[:172] = 0
    white[1364:] = 0
    dashes = [(x + w / 2, y + h / 2) for x, y, w, h, a in components(white) if 20 <= a <= 300 and max(w, h) <= 32]
    found = []
    for cx, cy in dashes:
        near = [(x, y) for x, y in dashes if abs(x - cx) < 90 and abs(y - cy) < 80]
        xs, ys = [p[0] for p in near], [p[1] for p in near]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if len(near) < 6 or not (60 <= x1 - x0 <= 95 and 35 <= y1 - y0 <= 85):
            continue
        inside = img[int(y0) + 12:int(y1) - 8, int(x0) + 12:int(x1) - 12]
        if not inside.size or inside.reshape(-1, 3).std(axis=0).max() > 14:  # a slot is plain inside
            continue
        edge = sum(min(x - x0, x1 - x, y - y0, y1 - y) <= 8 for x, y in near)
        c = (int(x0 + x1) // 2, int(y0 + y1) // 2)
        if edge >= 0.8 * len(near) and not blocked(*c) and all(abs(c[0] - a) > 50 or abs(c[1] - b) > 50 for a, b in found):
            found.append(c)
    for ax, ay in red_arrows(img):  # the arrow can hide the dashes: an arrow over a plain patch is a slot too
        tx, ty = ax + ICON_OFFSET, ay + ICON_OFFSET + 15
        p = img[ty - 15:ty + 15, tx - 20:tx + 20]
        if not blocked(tx, ty) and not blocked(ax, ay) and p.size and p.reshape(-1, 3).std(axis=0).max() < 10 and \
                all(abs(tx - a) > 50 or abs(ty - b) > 50 for a, b in found):
            found.append((tx, ty))
    return found


def floor(img, box=None):
    """A spot of free floor: the most common colour among the plain patches, away from an open panel and empty slots."""
    patches = {}
    empty = slots(img)
    for y in range(180, 1340, 40):
        for x in range(20, 680, 40):
            if blocked(x, y) or blocked(x + 40, y + 40) or any(abs(x + 20 - a) < 70 and abs(y + 20 - b) < 70 for a, b in empty):
                continue
            if box and box[0] - 30 < x + 20 < box[0] + box[2] + 30 and box[1] - 30 < y + 20 < box[1] + box[3] + 60:
                continue
            p = img[y:y + 40, x:x + 40]
            if p.reshape(-1, 3).std(axis=0).max() < 4 and p.mean() < 235:
                patches.setdefault(tuple(int(v) // 16 for v in p.mean(axis=(0, 1))), []).append((x + 20, y + 20))
    if not patches:
        return None
    spots = max(patches.values(), key=len)
    return spots[len(spots) // 2]


def screen(img):
    """'menu', 'panel', 'game' or 'unknown'. The game's bottom bar is lit only when nothing covers the game."""
    if img is None or img.shape[1] != 720 or img.shape[0] < 1500:
        return "unknown"
    x0, y0 = MENU_X[0] - 30, MENU_X[1] - 30
    red = mask(img[y0:y0 + 60, x0:x0 + 60], (0, 130, 170), (7, 255, 255))
    bar = all(np.count_nonzero(blue_mask(img[1420:1440, x - 10:x + 10])) > 40 for x in BAR_BUTTONS)
    if not bar:
        white = cv2.inRange(img[480:540, 80:400], (240, 240, 240), (255, 255, 255))
        return "menu" if np.count_nonzero(red) > 1200 and np.count_nonzero(white) > 8000 else "unknown"
    return "panel" if panel(img) else "game"


def icon(img, x, y):
    """The food icon at a tap point: how a station is known again, because coordinates change with scrolling."""
    return cv2.cvtColor(img[max(y - 30, 0):y + 30, max(x - 30, 0):x + 30], cv2.COLOR_BGR2GRAY)


def same_icon(a, b):
    if a.shape != b.shape or not a.size:
        return False
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0]) > 0.75


def moved(a, b):
    """Did the restaurant scroll between two frames? Small tiles of the first are looked for in the second, and they
    vote: a crowd walking around spoils some of them, but it fooled a comparison of the whole picture. No vote: yes."""
    ga, gb = (cv2.cvtColor(i, cv2.COLOR_BGR2GRAY) for i in (a, b))
    votes = []
    for y in range(300, 1200, 180):
        for x in (130, 300, 470):
            tile = ga[y:y + 90, x:x + 120]
            if tile.std() < 12:
                continue
            res = cv2.matchTemplate(gb[172:1360, x - 6:x + 126], tile, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score > 0.85:
                votes.append(loc[1] + 172 - y)
    return not votes or abs(np.median(votes)) >= 8


def at_end(img, up):
    """The empty area beyond the restaurant fills the strip next to the fixed bars: one plain colour (spread about 1, the
    restaurant itself above 40, measured 2026-11-06). Then a scroll that way cannot move, so the bot need not try."""
    y0, y1 = (178, 240) if up else (1270, 1345)
    return float(img[y0:y1, 110:600].reshape(-1, 3).astype(float).std(axis=0).max()) < 6


def same_view(a, b):
    """moved() says yes when nothing can be compared; this makes sure the two pictures really show the same place."""
    ga, gb = (cv2.cvtColor(i, cv2.COLOR_BGR2GRAY)[300:1200, 130:590] for i in (a, b))
    return float(cv2.matchTemplate(gb, ga[100:800, 20:440], cv2.TM_CCOEFF_NORMED).max()) > 0.7


def ocr(img, box):
    """[(x, y, w, h, text)] in a part of the picture, by macOS's Vision (eatventure_ocr.swift). [] if that fails."""
    binary, x, y = os.path.join(drive.STATE, "ocr"), max(box[0], 0), max(box[1], 0)
    try:
        if not os.path.exists(binary):
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eatventure_ocr.swift")
            subprocess.run(["swiftc", "-O", src, "-o", binary], check=True, capture_output=True)
        path = os.path.join(drive.STATE, "ocr_in.png")
        cv2.imwrite(path, img[y:y + box[3], x:x + box[2]])
        out = subprocess.run([binary, path], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    lines = []
    for l in out.splitlines():
        p = l.split(None, 4)
        if len(p) == 5:
            lines.append((int(p[0]) + x, int(p[1]) + y, int(p[2]), int(p[3]), p[4]))
    return lines


# Rows of the upgrades menu that drop crates: "+1 Koch", and tables by their title ("Kauf noch einen Tisch | +4 Kunden").
# Plain "+1 Kunde" and "+1 Auto" rows and speed-ups ("Koch wird schneller") drop none. German and English.
STAFF = re.compile(r"\+(\d+) (?:kellner|k[oö]ch|kassierer|barista|waiter|cook|chef|cashier)", re.I)
TABLE = re.compile(r"tisch|table", re.I)


def row_crates(text):
    """How many crates buying this row of the upgrades menu drops."""
    staff = STAFF.search(text)
    return int(staff.group(1)) if staff else 1 if TABLE.search(text) else 0


def menu_rows(img):
    """The text of each fully visible row of the upgrades menu, from the top."""
    lines = ocr(img, (100, 560, 400, 500))
    rows = [" | ".join(l[4] for l in lines if abs(l[1] + l[3] / 2 - y) < 58) for y in MENU_ROWS]
    return [r for r in rows if r]


GOOD_NAME = re.compile(r"^[A-ZÄÖÜ][\w\- ]{3,}$")


def read_station(img, box):
    """(name, level) from a station's panel, None where it cannot be read (say, cut off by the top bar)."""
    lines, level, name = ocr(img, box), None, None
    for x, y, w, h, t in lines:
        m = re.search(r"Level\s*(\d+)", t)
        if m:
            level = int(m.group(1))
            below = [l for l in lines if y + h / 2 < l[1] < y + 2.5 * h and abs(l[0] + l[2] / 2 - x - w / 2) < 70]
            name = min(below, key=lambda l: l[1])[4].strip() if below else None
            if name and not GOOD_NAME.match(name):  # text from what lies beside the panel, like an event's timer
                name = None
    return name, level


UNORDERED = 10 ** 9


class Memory:
    """What the bot learned, kept between runs: stations and how fast to click."""
    CADENCES = ["0.02", "0.05", "0.1", "0.2"]  # s after each click

    def __init__(self):
        self.path = os.path.join(drive.STATE, "bot_memory.json")
        self.d = {"stations": {}, "cadence": {c: {"n": 0, "rate": 0.0} for c in self.CADENCES}}
        try:
            self.d.update(json.load(open(self.path)))
        except (OSError, ValueError):
            pass
        self.d["stations"] = {k: v for k, v in self.d["stations"].items() if GOOD_NAME.match(k)}
        for i, st in enumerate(self.d["stations"].values(), 1):  # older files: they are kept in the order first seen
            st.setdefault("order", i)
        self.d.setdefault("max_level", 0)  # the level at which a station here showed MAX

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.d, f, indent=1)
        os.replace(tmp, self.path)

    def station(self, name, level, maxed, cost=None):
        known = difflib.get_close_matches(name, self.d["stations"], 1, 0.75)  # OCR: "Nrap" is Wrap, not a new station
        st = self.d["stations"].get(known[0] if known else name)
        if st is None:  # the order of unlocking, as far as the bot can see it: a station has a name once it was opened
            st = {"level": 0, "max": False, "order": 1 + max((s.get("order", 0) for s in self.d["stations"].values()), default=0)}
            self.d["stations"][name] = st
        if level is not None:
            st["level"] = level
        st["max"] = maxed  # as the panel shows it now: a renovation may open a station up again
        if cost:
            st["cost"] = cost
        if maxed and level:
            self.d["max_level"] = level
        self.save()

    def order_of(self, name):
        """When this station was unlocked: 1 is the first. Unknown comes last."""
        known = difflib.get_close_matches(name or "", self.d["stations"], 1, 0.75)
        return self.d["stations"][known[0]].get("order", UNORDERED) if known else UNORDERED

    def has_units(self):
        return any(re.search("[A-Za-z]", s.get("cost") or "") for s in self.d["stations"].values())

    def pick_cadence(self):
        """(seconds, measure?): try each a few times, then mostly the fastest, now and then another to check."""
        c = self.d["cadence"]
        least = min(self.CADENCES, key=lambda k: c[k]["n"])
        if c[least]["n"] < 3:
            return least, True
        if random.random() < 0.1:
            return random.choice(self.CADENCES), True
        return max(self.CADENCES, key=lambda k: c[k]["rate"]), False

    def learn_cadence(self, key, rate):
        c = self.d["cadence"][key]
        c["n"] += 1
        c["rate"] += (rate - c["rate"]) / min(c["n"], 10)  # the last ~10 count: prices and money change
        self.save()

    def unfinished(self):
        top = self.d["max_level"]  # a station at that level has no arrow, so nothing would ever look at it again
        return [k for k, v in self.d["stations"].items() if not v["max"] and not (top and v["level"] >= top)]

    def progress(self):
        st = self.d["stations"]
        return f"{len(st) - len(self.unfinished())}/{len(st)} maxed"


class Stop(Exception):
    pass


# Scroll gestures to compare from the web page: name -> (dy per gesture, pieces, hold after press, wait per piece, flicks).
# The restaurant moves opposite to the pointer: up = pointer moves down. The pointer returns unpressed afterwards.
SCROLLS = {  # name: dy, pieces, hold after press, wait per piece, flicks, pause before press, pause before release, x, y_up, y_down,
    # pause between flicks (and move the pointer back between them), jiggle the pointer before the first press, tap first
    "alt1": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    "alt2": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=2, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    "d200": dict(dy=200, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),   # alt1, a bigger jump
    "d300": dict(dy=300, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=400, yd=1000, gap=0.4, jiggle=False, tap=False),
    "d500": dict(dy=500, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=300, yd=1100, gap=0.4, jiggle=False, tap=False),
    "d800": dict(dy=800, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=250, yd=1150, gap=0.4, jiggle=False, tap=False),
    # swipes with acceleration (firmware `burst`: one report per number, 15 ms apart): slow start, fast release, so iOS
    # carries the momentum on like after a finger. The numbers are mouse counts per report; iOS accelerates fast reports.
    "f1": dict(burst=[5, 10, 20, 30, 40, 50], x=540, yu=400, yd=1100),
    "f2": dict(burst=[5, 10, 15, 25, 35, 50, 65, 80], x=540, yu=350, yd=1150),
    "f3": dict(burst=[10, 25, 45, 70, 100, 127], x=540, yu=300, yd=1200),
    "f4": dict(burst=[30, 60, 90, 127], x=540, yu=350, yd=1150),
    "f5": dict(burst=[3, 6, 9, 12, 15, 18, 21, 24, 27, 30], x=540, yu=400, yd=1100),
    # same start as alt1, only the length changes: where does a longer drag stop working?
    "e140": dict(dy=140, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    "e160": dict(dy=160, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    "e180": dict(dy=180, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    # chains: N short flicks (press, jump of 120, release, pointer back) in ONE serial session, `gap` s after each way back
    "c3a": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=3, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.10, jiggle=False, tap=False, chain=True),
    "c3b": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=3, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.25, jiggle=False, tap=False, chain=True),
    "c6a": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=6, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.10, jiggle=False, tap=False, chain=True),
    "c6b": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=6, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.25, jiggle=False, tap=False, chain=True),
    "c10a": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=10, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.10, jiggle=False, tap=False, chain=True),
    "alt3": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=3, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=False, tap=False),
    "dbl": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=2, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.0, jiggle=False, tap=False),    # no pause, no way back
    "wake": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=540, yu=500, yd=900, gap=0.4, jiggle=True, tap=False),   # a tiny move right before the press
    "tap": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=1, pre=0.0, end=0.0, x=400, yu=700, yd=700, gap=0.4, jiggle=False, tap=True),    # a click on plain floor first
    "left": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=2, pre=0.0, end=0.0, x=60, yu=560, yd=1100, gap=0.4, jiggle=False, tap=False),  # alt2 from the plain left strip
    "mid": dict(dy=120, pieces=1, hold=0.0, wait=0.03, flicks=2, pre=0.0, end=0.0, x=400, yu=700, yd=700, gap=0.4, jiggle=False, tap=False),   # alt2 from the kitchen floor
}


def scroll_gesture(name, up):
    """One scroll gesture from SCROLLS: go to the start point, then press, move in pieces and release in ONE serial
    session (board() closes the port after a call, and a press split from its release was let go)."""
    c = SCROLLS[name]
    y = c["yu"] if up else c["yd"]
    if "burst" in c:  # press, the accelerating reports, release: all in one serial session
        sign = 1 if up else -1
        prof = ",".join(str(sign * v) for v in c["burst"])
        drive.board(*drive.goto(c["x"], y))
        drive.board("press", f"burst {prof}", "release", wait=[0.03, 0.0, 0.02])
        time.sleep(0.3)
        drive.board("burst " + ",".join(str(-sign * v) for v in c["burst"]), wait=0.02)  # the pointer back, unpressed
        drive.forget_pointer()
        return
    piece = round((1 if up else -1) * c["dy"] / c["pieces"])
    drive.board(*drive.goto(c["x"], y))
    time.sleep(c["pre"])
    if c["jiggle"]:
        drive.board("move 4 0", "move -4 0", wait=0.0)
    if c["tap"]:
        drive.board("click", wait=0.15)
    if c.get("chain"):  # everything in one call: nothing for the board to release, no home between the flicks
        lines, waits = [], []
        for _ in range(c["flicks"]):
            lines += ["press", f"move 0 {piece}", "release", f"move 0 {-piece}"]
            waits += [0.02, c["wait"], 0.02, c["gap"]]
        drive.board(*lines, wait=waits)
        drive.forget_pointer()
        return
    for i in range(c["flicks"]):
        lines = ["press"] + [f"move 0 {piece}"] * c["pieces"] + ["release"]
        waits = [c["hold"] or 0.02] + [c["wait"]] * (c["pieces"] - 1) + [c["end"] or 0.02, 0.02]
        drive.board(*lines, wait=waits)
        if c["gap"] and i < c["flicks"] - 1:
            time.sleep(c["gap"])
            drive.board(f"move 0 {-piece * c['pieces']}", wait=0.02)
            time.sleep(c["gap"])
    drive.board(f"move 0 {-piece * c['pieces']}", wait=0.02)
    drive.forget_pointer()


class Aborted(Exception):
    """The web page asked to stop the running phase (or switched to manual while an automatic one ran)."""


MAP_MARKS = os.path.join(drive.STATE, "map_marks.jsonl")  # read by eatventure_map.py
MAP_LIVE = os.path.join(drive.STATE, "map_live.json")  # written by eatventure_ui.py after each picture
SAME_STATION = 70  # frame pixels on the map
MAP_REVISIT = 60.0  # a station out of view is visited at most this often: money comes in, but scrolling takes long
MAP_RESET = os.path.join(drive.STATE, "map_reset")  # touched here: eatventure_ui.py starts a new map
CONTROL = os.path.join(drive.STATE, "eatventure_control.json")  # written by eatventure_ui.py, read here
STATUS = os.path.join(drive.STATE, "eatventure_status.json")  # written here, read by eatventure_ui.py
STATUS_LOCK = threading.Lock()


class Bot:
    def __init__(self, clicks):
        self.clicks = clicks
        self.mem = Memory()
        self.names = []  # (icon, name) of the stations seen in this run
        self.done = []  # (icon, time) of the stations that had a turn lately: no rounds, only a short cooldown
        self.done_at = {}  # (cell) -> time, same for positions: smoke and animations can hide an icon
        self.slot_wait = {}  # position -> time to try an empty slot again (not affordable yet)
        self.crate_tries = {}
        self.acted = False
        self.end_frames = {}  # 'top'/'bottom' -> the picture there, learned once per restaurant
        self.owed = 0  # crates still to open from menu purchases (waiters, tables, cooks, cashiers)
        self.flicks = 0
        self.counts = {"turns": 0, "clicks": 0, "menu": 0, "crates": 0, "slots": 0, "next": 0}

    def log(self, *a):
        print(time.strftime("%H:%M:%S"), *a, flush=True)
        self.event("log", " ".join(str(x) for x in a))

    def event(self, kind, text, why=None, img=None):
        """One line in events.jsonl for the web page (eatventure_ui.py): what, why, when, and a small picture of what the
        bot saw. Pictures are kept for the last 400 events only."""
        d = os.path.join(drive.STATE, "eatventure_events")
        os.makedirs(d, exist_ok=True)
        if not hasattr(self, "seq"):  # go on from the last run: pictures must not overwrite older ones
            try:
                with open(os.path.join(d, "events.jsonl")) as f:
                    self.seq = sum(1 for _ in f)
            except OSError:
                self.seq = 0
        self.seq += 1
        pic = None
        if img is not None:
            pic = f"{self.seq:06d}.jpg"
            cv2.imwrite(os.path.join(d, pic), cv2.resize(img, (360, int(img.shape[0] * 360 / img.shape[1]))),
                        [cv2.IMWRITE_JPEG_QUALITY, 70])
            old = os.path.join(d, f"{self.seq - 400:06d}.jpg")
            if os.path.exists(old):
                os.remove(old)
        with open(os.path.join(d, "events.jsonl"), "a") as f:
            f.write(json.dumps({"n": self.seq, "t": time.time(), "kind": kind, "text": text, "why": why, "pic": pic,
                                "counts": self.counts, "maxed": self.mem.progress()}) + "\n")

    def decide(self, action, why, img=None):
        print(time.strftime("%H:%M:%S"), "->", action, "|", why, flush=True)
        self.event("decide", action, why, img if img is not None else self.frame())

    def frame(self):
        for _ in range(5):
            img = cv2.imread(os.path.join(drive.STATE, "live.png"))
            if img is not None:
                return img
            time.sleep(0.05)
        raise Stop("no picture in live.png")

    def stop_unknown(self, why):
        self.set_status(busy=None, stopped=why)
        self.event("stop", why, "the bot stopped and waits for a person", self.frame())
        cv2.imwrite(os.path.join(drive.STATE, "bot_stop.png"), self.frame())
        raise Stop(why)

    def guard(self):
        """Never click into something else: a call screen came up on 2026-11-06, a tap landed beside its Accept button.
        The picture lags, so this makes such a hit rare, not impossible."""
        img = self.frame()
        if screen(img) == "unknown" and not dialog_button(img):
            self.stop_unknown("not the game: no more clicks")

    def click(self, x, y, n=1, wait=0.03):
        self.guard()
        drive.board(*drive.goto(x, y), *["click"] * n, wait=wait)

    def tap(self, x, y):
        if blocked(x, y):  # not worth stopping the game for: what it should open just does not open
            self.log(f"no tap at {x},{y}: fixed UI lies there")
            return
        self.click(x, y)

    def wait_for(self, wanted, timeout=SHOW):
        """Poll the picture until the screen is one of `wanted`; returns the frame, or None."""
        end = time.time() + timeout
        while time.time() < end:
            img = self.frame()
            if screen(img) in wanted:
                return img
            time.sleep(0.25)
        return None

    def to_game(self):
        """Close what is open. Stops on a screen it does not know, after giving a passing animation a moment."""
        for _ in range(10):  # an iOS banner can hold up the picture for a while
            img = self.frame()
            s = screen(img)
            if s == "game":
                return img
            if s == "menu":
                self.tap_menu_x()
            elif s == "panel":
                spot = floor(img, panel(img))
                if not spot:
                    self.stop_unknown("no free floor to close the panel")
                self.tap(*spot)
            else:
                time.sleep(SETTLE)
                continue
            if self.wait_for(("game",)) is not None:
                return self.frame()
        self.stop_unknown("cannot get back to the restaurant, unknown screen")

    def tap_menu_x(self):
        self.click(*MENU_X)

    def open_crate(self, x, y):
        key = (x // 40, y // 40)
        self.crate_tries[key] = self.crate_tries.get(key, 0) + 1
        self.log("crate", (x, y))
        self.tap(x, y)
        self.counts["crates"] += 1
        self.acted = True
        time.sleep(SETTLE)

    def buy_menu(self):
        """Buy what is blue in the upgrades menu. A coin on the button means it costs money, not gems or an ad."""
        self.click(*UPGRADES)
        if self.wait_for(("menu",)) is None:
            self.log("upgrades menu did not open, trying the button once more")  # a tap can miss: the pointer drifts
            self.to_game()
            self.click(*UPGRADES)
            if self.wait_for(("menu",)) is None:
                self.stop_unknown("the upgrades menu did not open (twice)")
        bought = 0
        for _ in range(20):  # what was bought disappears, the next one moves up to the top row
            img = self.frame()
            if screen(img) != "menu":  # the picture may lag, or a purchase may have moved the game on
                if self.wait_for(("menu", "game"), timeout=SHOW) is None or screen(self.frame()) != "menu":
                    break
                img = self.frame()
            y = MENU_ROWS[0]
            b = img[y - 25:y + 25, MENU_COIN[0]:MENU_COIN[1]]
            rows = menu_rows(img)
            cv2.imwrite(os.path.join(drive.STATE, "menu_last.png"), img)
            self.log("menu rows:", " / ".join(rows)[:140], f"-> {self.owed} crates owed" if self.owed else "")
            # still a blue button with a coin on it? (not grey, not gems, not an ad)
            if not rows or not (np.count_nonzero(blue_mask(b)) > 0.4 * b.shape[0] * b.shape[1] and
                                np.count_nonzero(mask(b, (14, 120, 180), (28, 255, 255))) > 100):
                break
            if row_crates(rows[0]):  # one at a time, and only a purchase that shows counts
                self.click(MENU_BUY_X, y)
                bought += 1
                end = time.time() + SHOW
                while time.time() < end and (menu_rows(self.frame()) or rows)[0] == rows[0]:
                    time.sleep(0.5)
                if (menu_rows(self.frame()) or rows)[0] == rows[0]:
                    self.log("menu: the top row did not change, leaving the menu")
                    break
                self.owed += row_crates(rows[0])
                continue
            # up to the first row that drops crates: those are counted one by one above
            n = next((i for i, r in enumerate(rows) if row_crates(r)), len(rows))
            self.click(MENU_BUY_X, y, n, 0.35)
            bought += n
            time.sleep(SETTLE)
        self.log("menu: tapped", bought, "times on the top row")
        self.counts["menu"] += bought
        self.acted = self.acted or bought > 0
        self.to_game()
        return bought

    def unlock(self, x, y):
        """A new station, right after the crates. Its crate comes up next by itself."""
        self.slot_wait[(x // 40, y // 40)] = time.time() + SLOT_RETRY
        self.tap(x, y)
        opened = self.wait_for(("panel",))
        b = panel_button(opened) if opened is not None else None
        if b and not is_unlock(opened, b):  # an arrow over a plain floor looks like a slot: it is a station, give it its turn
            self.to_game()
            img = self.frame()
            self.decide(f"station turn at {x},{y}", "looked like a slot, but its panel is a station's", img)
            self.turn(x, y, img)
            return
        if not b or b[0] != "blue" or b[3] < 100 or blocked(b[1], b[2]):
            self.log("slot", (x, y), "not affordable" if b else "no panel")
            self.to_game()
            return
        self.click(b[1], b[2])
        self.log("slot", (x, y), "unlocked")
        self.counts["slots"] += 1
        self.acted = True

    def mark(self, **kw):
        """Tell the map (eatventure_map.py) what only the bot knows. Coordinates are those of the current picture."""
        with open(MAP_MARKS, "a") as f:
            f.write(json.dumps(kw) + "\n")

    def note_panel(self, ic, x, y, name, maxed, bx, by):
        """At the end of a turn, panel open: the level and the price of the next upgrade, for the map and the next round.
        The name too if the first look missed it: that one can come while the panel is still opening."""
        drive.board(*drive.goto(bx, by - 110))  # off the button: the pointer hides the price
        time.sleep(SETTLE)
        img = self.frame()
        b = panel_button(img)
        if not b:
            return None
        read_name, level = read_station(img, b[4])
        cost = None if maxed else button_price(img, b)
        if cost and cost.replace(".", "").isdigit() and self.mem.has_units():  # a part of a price under something moving
            cost = None
        if not name and read_name:
            name = read_name
            self.names.append((ic, name))
        if not name:
            return cost
        self.mem.station(name, level, maxed, cost)
        self.mark(kind="station", x=x, y=y, name=name, level=level, max=maxed, cost=cost, order=self.mem.order_of(name))
        return cost

    def really_max(self, bx, by):
        """A grey button with no coin on it? The pointer shows in the picture and can sit on the coin, so look again with
        the pointer moved up, off the button (2026-11-06: "Wrap MAX" at level 43, 79 and 88, all of them wrong)."""
        drive.board(*drive.goto(bx, by - 110))
        time.sleep(SETTLE)
        b = panel_button(self.frame())
        return bool(b) and b[0] == "grey" and b[3] < 100

    def turn(self, x, y, img):
        """One station's turn: tap it, click its button while it stays blue, close the panel."""
        ic = icon(img, x, y)
        self.tap(x, y)
        self.done.append((ic, time.time()))  # also when nothing opens: the next pass must not hang on this arrow
        self.done_at[(x // 40, y // 40)] = time.time()
        opened = self.wait_for(("panel",))
        if opened is None:
            self.log("station", (x, y), "no panel opened")
            return
        button = panel_button(opened)
        if not button:
            self.stop_unknown("a panel without a known button")
        kind, bx, by, gold, box = button
        name, level = read_station(opened, box)
        maxed = kind == "grey" and gold < 100 and self.really_max(bx, by)
        if name:
            self.names.append((ic, name))
            self.mem.station(name, level, maxed)
            self.mark(kind="station", x=x, y=y, name=name, level=level, max=maxed)
        if kind == "grey":
            cost = self.note_panel(ic, x, y, name, maxed, bx, by)
            self.log("station", name or (x, y), level, "MAX" if maxed else f"not affordable, next {cost}", self.mem.progress())
            self.to_game()
            return
        if gold < 100:
            self.stop_unknown("a blue button without a coin: it may cost gems or play an ad")
        if blocked(bx, by):
            self.log("station", (x, y), "button under fixed UI")
            self.to_game()
            return
        if is_unlock(opened, button):
            self.done.pop()  # a new station does not use up a turn (eatventure.md)
            self.done_at.pop((x // 40, y // 40), None)
            self.click(bx, by)
            self.log("slot", (x, y), "unlocked")
            self.counts["slots"] += 1
            self.acted = True
            return
        # everything affordable at once. The picture lags, so some clicks land on the grey button: they do nothing
        key, measure = self.mem.pick_cadence()
        started = time.time()
        self.click(bx, by, 5, float(key))
        clicks = 5
        while clicks < self.clicks:
            b = panel_button(self.frame())
            if not b or b[0] != "blue" or abs(b[1] - bx) > 15 or abs(b[2] - by) > 15:
                if b and b[0] == "grey" and b[3] < 100 and name and self.really_max(bx, by):  # no price left on it
                    self.mem.station(name, read_station(self.frame(), b[4])[1], True)
                    self.log("station", name, "MAX", self.mem.progress())
                break
            self.click(bx, by, 5, float(key))
            clicks += 5
        took = time.time() - started
        after = None
        if measure and name and level is not None:
            time.sleep(2.5)  # until the picture shows the last click
            img = self.frame()
            b = panel_button(img)
            after = read_station(img, b[4]) if b else None
        if after and after[0] == name and after[1] is not None and after[1] >= level:
            rate = (after[1] - level) / took
            self.mem.station(name, after[1], False)  # a MAX was handled in the loop above
            if clicks >= 10:
                self.mem.learn_cadence(key, rate)
            self.log("station", name, level, "->", after[1], f"{rate:.1f} levels/s at wait {key}", clicks, "clicks")
        else:
            self.log("station", name or (x, y), level, "clicks", clicks, "wait", key)
        cost = self.note_panel(ic, x, y, name, False, bx, by)
        self.log("station", name or (x, y), "next upgrade costs", cost)
        self.counts["turns"] += 1
        self.counts["clicks"] += clicks
        self.acted = True
        self.to_game()

    def new_place(self):
        """A flight or a renovation: other food, other stations, another look. Nothing known about the old place holds."""
        self.mem.d["stations"], self.mem.d["max_level"], self.names = {}, 0, []
        self.mem.save()
        self.end_frames, self.slot_wait, self.map_seen = {}, {}, {}
        self.done, self.done_at, self.crate_tries = [], {}, {}
        self.owed, self.search_after, self.search_up = 0, 0, True
        with open(MAP_RESET, "w") as f:  # eatventure_ui.py starts a new map
            f.write(str(time.time()))

    def go_next(self):
        """Build or fly: the goal, whenever the button is active. What comes after it is new: stop if it is unknown."""
        self.log("next stage: opening the button at the bottom left")
        self.click(*NEXT)
        end, spot = time.time() + SHOW, None
        while time.time() < end and not spot:
            spot = dialog_button(self.frame())
            time.sleep(0.25)
        if not spot:
            self.stop_unknown("no build/fly dialog, or its button shows a gem or another symbol")
        text = " ".join(l[4] for l in ocr(self.frame(), (80, 390, 560, 110)))
        self.log("next stage: pressing", spot, text)
        if "Fliege" in text or "Renovieren" in text:
            self.new_place()
        self.click(*spot)
        self.counts["next"] += 1
        self.acted = True
        end = time.time() + 30
        while time.time() < end:  # a plain button after it is a welcome, confirm it; anything else stops below
            img = self.frame()
            if screen(img) == "game":
                break
            spot = dialog_button(img)
            if spot:
                time.sleep(SETTLE)  # the picture may still show the button just pressed
                spot = dialog_button(self.frame())
                if spot:
                    self.log("next stage: confirming", spot)
                    self.click(*spot)
                    time.sleep(SETTLE)
            time.sleep(0.25)
        else:
            self.stop_unknown("a new screen after build/fly")
        time.sleep(SETTLE)
        return True

    def cooling(self, ic, x, y):
        """True while this station had its turn less than COOL seconds ago."""
        now = time.time()
        cool = getattr(self, "cool_for", COOL)  # a whole phase in phase mode: one turn per station and phase
        self.done = [(d, t) for d, t in self.done if now - t < cool]
        self.done_at = {k: t for k, t in self.done_at.items() if now - t < cool}
        return (x // 40, y // 40) in self.done_at or any(same_icon(ic, d) for d, _ in self.done)

    def known_cost(self, ic):
        """What the next upgrade of this icon's station cost when last read, as something to sort by. Unknown comes last."""
        for other, name in self.names:
            if same_icon(ic, other):
                key = cost_key(self.mem.d["stations"].get(name, {}).get("cost"))
                if key[0] >= 0:
                    return key
        return (UNORDERED, 0.0)

    def known_order(self, ic):
        for other, name in self.names:
            if same_icon(ic, other):
                return self.mem.order_of(name)
        return UNORDERED

    def known_level(self, ic):
        """The level the memory has for this icon; -1 for an unknown station, so that it gets read once."""
        for other, name in self.names:
            if same_icon(ic, other):
                return self.mem.d["stations"].get(name, {}).get("level", -1)
        return -1

    def step(self):
        """One action from the top of the loop in eatventure.md. False when the visible screen has nothing to do."""
        img = self.to_game()
        for x, y in crates(img):  # always first
            if self.crate_tries.get((x // 40, y // 40), 0) < 2:
                self.decide(f"open crate at {x},{y}", "a crate is on screen: crates come first, nothing works until they are open", img)
                self.open_crate(x, y)
                return True
        now = time.time()
        for x, y in slots(img):  # next: new stations
            arrow = any(abs(x - ax) < 110 and abs(y - ay) < 110 for ax, ay in red_arrows(img))
            if arrow or self.slot_wait.get((x // 40, y // 40), 0) < now:
                self.decide(f"unlock slot at {x},{y}", "an empty slot " + ("has a red arrow: affordable" if arrow else "was not tried for a while"), img)
                self.unlock(x, y)
                return True
        if next_active(img):
            self.decide("build / fly button", "the button at the bottom left is active (red arrow)", img)
        if next_active(img) and self.go_next():
            return True
        if badge(img):
            self.decide("upgrades menu", "the arrow badge on the menu button: something is affordable", img)
        if badge(img) and self.buy_menu():
            return True
        img = self.frame()
        for x, y in sorted(stations(img), key=lambda p: self.known_level(icon(img, *p))):  # lowest level first
            ic = icon(img, x, y)
            if not self.cooling(ic, x, y):
                lvl = self.known_level(ic)
                self.decide(f"station turn at {x},{y}", f"red arrow = affordable; lowest known level first (level {lvl if lvl >= 0 else '?'}, {len(stations(img))} with arrows, others cooling {COOL:.0f} s)", img)
                self.turn(x, y, img)
                return True
        return False

    def run(self, minutes):
        """Look at the screen over and over (every WATCH s at the latest) and do the first thing from the priority list in
        step(): crates, slots, build/fly, upgrades menu, stations with an arrow. No rounds and no pauses: when nothing on
        the visible part is to do, scroll on (down to the end, back up to the end, and so on) and look again."""
        end = time.time() + minutes * 60
        up, ends, flicks, sweep_start = False, 0, 0, time.time()  # down first: the kitchen is at the bottom
        last, last_coins, last_at = dict(self.counts), None, time.time()
        try:
            while time.time() < end:
                if self.counts != last:
                    last, last_coins, last_at = dict(self.counts), None, time.time()
                elif time.time() - last_at > STALL:  # no action for a while: fine if money comes in, else it is stuck
                    coins = " ".join(l[4] for l in ocr(self.frame(), (140, 90, 190, 50)))
                    if coins == last_coins:
                        self.stop_unknown(f"stuck: no action and no money for {STALL:.0f} s, coins {coins}")
                    last_coins, last_at = coins, time.time()
                if self.step():
                    continue
                before = self.to_game()
                self.decide("scroll " + ("up" if up else "down"), "nothing to do on the visible part: look further " + ("up" if up else "down"), before)
                self.flicks += 1  # keep what it saw when it decided to scroll: did it miss something?
                cv2.imwrite(os.path.join(drive.STATE, f"bot_flick_{self.flicks % 12:02d}.png"), before)
                drive.flick(*((540, 500, 120) if up else (540, 900, -120)), SCROLL_FLICKS)
                time.sleep(FLICK_SHOW)
                flicks += 1
                if not (moved(before, self.frame()) and flicks < 22):
                    up, ends, flicks = not up, ends + 1, 0
                    time.sleep(WATCH)
                if ends == 2:
                    self.log(f"pass over in {time.time() - sweep_start:.0f} s;", self.counts, self.mem.progress())
                    self.crate_tries, ends, up, sweep_start = {}, 0, False, time.time()
        finally:
            self.log("totals", self.counts, self.mem.progress(), self.mem.d["cadence"])

    # ---- phase mode: two timed phases, each followed by a crate pass ------------------------------------------------
    def walk(self, fn, ways=(False, True), until=None):
        """Scroll through the restaurant, by default down to the very bottom and then up to the very top (ways: False is
        down, True is up), and run fn(img) at every stop until it has nothing more to do there (fn returns True when it
        acted). A scroll that does not move the picture means the end. until() true: leave after the current stop. Returns
        which ends were reached."""
        reached = {}
        for up in ways:
            way = "top" if up else "bottom"
            flicks, reached[way] = 0, False
            while flicks < 40:
                while fn(self.to_game()):
                    self.check_abort()
                self.check_abort()
                if until and until():
                    return reached
                before = self.to_game()
                end_img = self.end_frames.get(way)
                if at_end(before, up) or (end_img is not None and not moved(end_img, before) and same_view(end_img, before)):
                    reached[way] = True
                    self.log(f"reached the {way} of the restaurant (the picture shows it, no scroll needed)")
                    break
                scroll_gesture(WALK_SCROLL, up)
                time.sleep(FLICK_SHOW)
                if not self.moved_yet(before):  # scrolling is reliable since the firmware fix: no change is the end
                    reached[way] = True
                    self.end_frames[way] = self.frame()  # next time this view is known as the end at once
                    self.log(f"reached the {way} of the restaurant (no change after a scroll)")
                    while fn(self.to_game()):  # the last stop at the end gets its look too
                        self.check_abort()
                    break
                flicks += 1
            if not reached[way]:
                self.log(f"the {way} was NOT reached after {flicks} scrolls")
        return reached

    def crate_fn(self, img, why="a menu purchase drops crates: waiters, tables or cooks were bought"):
        for x, y in crates(img):
            tries = self.crate_tries.get((x // 40, y // 40), 0)
            if tries < 2:
                if not tries:
                    self.owed = max(self.owed - 1, 0)
                self.decide(f"open crate at {x},{y}", why, img)
                self.open_crate(x, y)
                return True
        return False

    def station_fn(self, img):
        """New stations first, then every station with an arrow once, lowest level first."""
        now = time.time()
        for x, y in slots(img):
            if self.slot_wait.get((x // 40, y // 40), 0) < now:  # after a miss: leave it, an arrow there is tried as a station
                self.decide(f"unlock slot at {x},{y}", "stations phase: new stations come first", img)
                self.unlock(x, y)
                return True
        first = getattr(self, "round_first", False)  # a round begins with the cheapest, then goes on in the order of unlocking
        rank = self.known_cost if first else self.known_order
        for x, y in sorted(stations(img), key=lambda p: rank(icon(img, *p))):
            ic = icon(img, x, y)
            if not self.cooling(ic, x, y):
                self.round_first = False
                self.decide(f"station turn at {x},{y}", "stations phase: " + ("the cheapest begins the round" if first else "next in the order of unlocking"), img)
                self.turn(x, y, img)
                return True
        return False

    def owed_crates(self):
        """Open the crates of what the menu sold: the ones in view first. Only if some are still missing then, go through
        the restaurant, up and back down to the stations, and only until the last one is found."""
        self.crate_tries = {}
        end = time.time() + SHOW  # they take a moment to show, and the game may scroll to them by itself
        while time.time() < end:
            if self.crate_fn(self.to_game()):
                end = time.time() + (SHOW if self.owed else 0)
            elif not self.owed:
                break
            else:
                time.sleep(0.25)
        if not self.owed:
            return self.log("all crates of the menu purchases were in view: no scrolling")
        self.log(f"{self.owed} crates of the menu purchases not in view: going through the restaurant")
        self.walk(self.crate_fn, ways=(True, False), until=lambda: not self.owed)
        if self.owed:
            self.log(f"{self.owed} crates were not found in the whole restaurant")
        self.owed = 0

    def at_stop(self, img):
        """What a stop of the stations phase does: slots and every station until its button is no longer blue, one after
        the other. Only then the crates in view, also those of new slots and stars (user, 2026-11-06)."""
        return self.station_fn(img) or self.crate_fn(img, "the stations here are done: now the crates")

    def map_now(self):
        """What eatventure_ui.py knows right now, None if the page does not run. pos is None while the map does not
        recognise the picture: its last position is then wrong, and so is anything worked out from it."""
        try:
            with open(MAP_LIVE) as f:
                m = json.load(f)
        except (OSError, ValueError):
            return None
        if time.time() - m.get("t", 0) >= 8:
            return None
        if m.get("state") in ("lost", "start", "new map"):
            m["pos"] = None
        return m

    def cheapest_first(self):
        """If the map says the cheapest station is out of view, begin the round there."""
        seen = self.__dict__.setdefault("map_seen", {})
        for _ in range(6):
            self.check_abort()
            m = self.map_now()
            if not m or m.get("pos") is None:
                return
            priced = [s for s in m["items"] if s["kind"] == "station" and not s.get("max") and s.get("cost")]
            if not priced:
                return
            s = min(priced, key=lambda i: cost_key(i["cost"]))
            top, bottom = m["pos"], m["pos"] + m["view"]
            key = (s["x"] // SAME_STATION, s["y"] // SAME_STATION)
            if top + 40 <= s["y"] <= bottom - 40 or time.time() - seen.get(key, 0) < MAP_REVISIT:
                return
            self.decide(f"to {s.get('name')}, the cheapest at {s['cost']}", f"the map knows it at y {s['y']}, the view is {top}-{bottom}", self.frame())
            if not self.scroll_finds(s["y"] < top)[0]:
                seen[key] = time.time()
                return

    def map_stop(self, img):
        """A stop of the scan: stay until the map has this place. The picture can come as slowly as one frame in 5 s, and the
        map takes only still pictures: without this it gets the two ends and nothing between them."""
        time.sleep(1.0)  # the state may still be from before the scroll
        end = time.time() + 20
        while time.time() < end:
            m = self.map_now()
            if m and m.get("state") == "here":
                break
            self.check_abort()
            time.sleep(0.5)
        else:
            self.log("map: this stop did not get onto the map within 20 s")
        return False

    def moved_yet(self, before):
        """Did the last scroll move the picture? It can come as slowly as one frame in 5 s, so an end is only called after
        waiting for it. No second scroll."""
        end = time.time() + 2 * FLICK_SHOW
        while time.time() < end:
            if moved(before, self.frame()):
                return True
            time.sleep(0.5)
        return moved(before, self.frame())

    def scroll_finds(self, up):
        """One scroll, then the work at the new place. Returns (did the picture move, was anything done there)."""
        before = self.to_game()
        scroll_gesture(WALK_SCROLL, up)
        time.sleep(FLICK_SHOW)
        if not self.moved_yet(before):
            return False, False
        done = dict(self.counts)
        while self.at_stop(self.to_game()):
            self.check_abort()
        return True, self.counts != done

    def stations_on_map(self):
        """The map first: go to the crates and the stations not at max it knows and the picture does not show. Without a
        position, or while stations the bot knows are missing on the map, look further until something turns up: that
        also brings the map up to date. A whole trip without anything: wait a minute, then money is what is missing."""
        seen = self.__dict__.setdefault("map_seen", {})
        ends = 0
        for _ in range(30):
            self.check_abort()
            m = self.map_now()
            pos = m.get("pos") if m else None
            due = []
            if pos is not None:
                top, bottom, now = pos, pos + m["view"], time.time()
                for s in m["items"]:
                    if s["kind"] not in ("station", "crate") or s.get("max"):
                        continue
                    key = (s["x"] // SAME_STATION, s["y"] // SAME_STATION)
                    if top + 40 <= s["y"] <= bottom - 40:
                        seen[key] = now
                    elif now - seen.get(key, 0) > MAP_REVISIT:
                        due.append((abs(s["y"] - (top + bottom) / 2), s, key))
            if due:
                _, s, key = min(due, key=lambda d: (self.mem.order_of(d[1].get("name")), d[0]))
                up = s["y"] < top
                self.decide(f"to {s.get('name') or s['kind']} at map y {s['y']}", f"the map knows it, the view is {top}-{bottom}", self.frame())
                if not self.scroll_finds(up)[0]:  # the end: the map is off, leave this one for a while
                    seen[key] = time.time()
                continue
            known = sum(s["kind"] == "station" for s in m["items"]) if m else 0
            if pos is not None and known >= max(len(self.mem.d["stations"]), 1):
                return  # everything on the map was seen a moment ago
            if time.time() < getattr(self, "search_after", 0):
                return
            up = getattr(self, "search_up", True)
            if at_end(self.to_game(), up):
                moved_on, found = False, False
            else:
                self.log("looking further", "up" if up else "down", "the map does not know where the view is" if pos is None else
                         f"the map has {known} of {len(self.mem.d['stations'])} known stations")
                moved_on, found = self.scroll_finds(up)
            if found:
                return
            if not moved_on:
                self.search_up, ends = not up, ends + 1
                if ends >= 2:
                    self.search_after = time.time() + MAP_REVISIT
                    self.log("a whole trip without anything to do: looking again in a minute")
                    return

    def fly_check(self):
        """Build or fly when the button is active. The plane is only active once every station is at max: no check first."""
        img = self.to_game()
        if not next_active(img):
            return False
        self.decide("build / fly button", "the button at the bottom left is active (red arrow)", img)
        return self.go_next()

    def truck(self):
        """A red "!" on the truck: accept the game's best order, never its x2 button (an ad). The dialog dims the game, so
        click()'s guard does not fit: each tap goes out only after reading this dialog's title in the current picture."""
        if time.time() < getattr(self, "truck_after", 0) or not icon_ready(self.to_game(), TRUCK_BADGE):
            return
        self.decide("food delivery", "the truck icon shows a red !", self.frame())
        self.click(*TRUCK)
        end, lines = time.time() + SHOW, []
        while time.time() < end and not any("Essenslieferung" in l[4] for l in lines):
            lines = ocr(self.frame(), (70, 280, 590, 1020))
            time.sleep(0.5)
        if not any("Essenslieferung" in l[4] for l in lines):  # a tap can miss: not worth stopping the game for
            self.log("food delivery: the dialog did not open, trying again later")
            self.truck_after = time.time() + 30
            self.to_game()
            return
        rows = sorted((l for l in lines if "Akzeptieren" in l[4]), key=lambda l: l[1])
        deal = [l for l in lines if "Bester Deal" in l[4]]
        below = [l for l in rows if deal and l[1] > deal[0][1]]
        self.truck_after = time.time() + TRUCK_RETRY
        if rows:
            row = below[0] if below else rows[-1]  # the rows are sorted, and the last one paid most so far
            self.log("food delivery: accepting", "the best deal" if below else "the last row", [l[4] for l in lines if re.fullmatch(r"[\d.,]+[A-Za-z]{1,2}", l[4])])
            drive.board(*drive.goto(row[0] + row[2] // 2, row[1] + row[3] // 2), "click")
        else:
            cv2.imwrite(os.path.join(drive.STATE, "truck_unknown.png"), self.frame())
            self.log("food delivery: nothing to accept, closing it (truck_unknown.png):", " | ".join(l[4] for l in lines)[:140])
            drive.board(*drive.goto(*TRUCK_X), "click")
        if self.wait_for(("game", "panel")) is None:
            self.stop_unknown("the food delivery dialog did not close")

    def helper(self):
        """A red "!" on the person icon: invite the helper with the most red items, then the highest levels, never with
        +4m (an ad). Like truck(): each tap goes out only after reading this dialog's title in the current picture."""
        if time.time() < getattr(self, "helper_after", 0) or not icon_ready(self.to_game(), HELPER_BADGE):
            return
        self.decide("invite a helper", "the person icon shows a red !", self.frame())
        self.click(*HELPER)
        title = lambda ls: any("Helfer einladen" in l[4] for l in ls)
        end, lines = time.time() + 2 * SHOW, []  # its list shows "Lädt..." first, then the helpers
        while time.time() < end and not (title(lines) and any("Einladen" in l[4] for l in lines)):
            img = self.frame()
            lines = ocr(img, (40, 400, 640, 790))
            time.sleep(0.5)
        if not title(lines):  # a tap can miss: not worth stopping the game for
            self.log("helper: the dialog did not open, trying again later")
            self.helper_after = time.time() + 30
            self.to_game()
            return
        self.helper_after = time.time() + TRUCK_RETRY
        rows = [(l, helper_items(img, l[1] + l[3] // 2 + 24)) for l in lines if "Einladen" in l[4]]
        if any(lv is None for _, items in rows for _, lv in items):
            cv2.imwrite(os.path.join(drive.STATE, "helper_digits.png"), img)
            self.log("helper: a level could not be read and does not count (helper_digits.png)")
        self.log("helper scores (red, levels):", [helper_score(items) for _, items in rows])
        best = max(rows, key=lambda r: helper_score(r[1]), default=None)
        if best and helper_score(best[1])[1]:
            self.log("helper: inviting row", rows.index(best) + 1, best[1])
            drive.board(*drive.goto(best[0][0] + best[0][2] // 2, best[0][1] + best[0][3] // 2), "click")
        else:
            cv2.imwrite(os.path.join(drive.STATE, "helper_none.png"), img)
            self.log("helper: nobody to invite, closing it (helper_none.png):", " | ".join(l[4] for l in lines)[:140])
            x = dialog_x(img)
            if not x:
                self.stop_unknown("no close button on the helper dialog")
            drive.board(*drive.goto(*x), "click")
        if self.wait_for(("game", "panel")) is None:
            self.stop_unknown("the helper dialog did not close")

    def control(self):
        try:
            with open(CONTROL) as f:
                c = json.load(f)
        except (OSError, ValueError):
            c = {}
        c.setdefault("mode", "auto")
        c.setdefault("x_stations", 60)
        c.setdefault("x_menu", 60)
        c.setdefault("cmds", [])
        return c

    def set_status(self, **kw):
        with STATUS_LOCK:
            st = getattr(self, "status", {})
            st.update(kw)
            st["t"] = time.time()
            self.status = st
            tmp = STATUS + ".tmp"
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, STATUS)

    def heartbeat(self):
        """The page calls the bot dead when the status is older than 15 s, and a phase can run for minutes."""
        while True:
            time.sleep(2.0)
            try:
                self.set_status()
            except OSError:
                pass

    def check_abort(self):
        """Called at every stop of a walk: leave the running phase when the page says so."""
        if time.time() - getattr(self, "_chk", 0) < 0.5:
            return
        self._chk = time.time()
        c = self.control()
        for cmd in c["cmds"]:
            if cmd["id"] > self.last_id and cmd["cmd"] == "abort":
                self.last_id = cmd["id"]
                raise Aborted("abort pressed")
        if c["mode"] == "manual" and self.running_auto:
            raise Aborted("switched to manual")

    def phase(self, name, auto):
        """One phase by name: stations, menu, crates or fly."""
        t0 = time.time()
        self.running_auto = auto
        self.set_status(busy=name, since=t0, auto=auto, stopped=None)
        try:
            if name.startswith("scroll:"):
                _, variant, way = name.split(":")
                self.decide(f"scroll test {variant} {way}", "pressed on the page", self.frame())
                t1 = time.time()
                scroll_gesture(variant, way == "up")
                self.log(f"scroll {variant} {way}: the gesture took {time.time() - t1:.1f} s")
                return
            if name == "fly":
                img = self.to_game()
                self.decide("manual: build / fly", "pressed on the page", img)
                if not next_active(img):
                    self.log("fly: nothing to do now: the button at the bottom left has no red arrow (nothing to build or fly yet)")
                else:
                    self.fly_check()
                return
            self.decide(f"phase: {name}", ("automatic" if auto else "started on the page") + f" ({name})", self.frame())
            if name == "map":  # only looks: eatventure_ui.py paints the map from the picture while this scrolls
                self.mark(kind="scan", on=True)  # keeps the map and what is on it: only a flight or a renovation clears it
                try:
                    self.mark(kind="ends", **self.walk(self.map_stop, ways=(True, False)))
                finally:
                    self.mark(kind="scan", on=False)
                self.log(f"map phase done in {time.time() - t0:.0f} s")
                return
            if name != "crates":
                self.fly_check()
                self.truck()
                self.helper()
            if name == "stations":
                self.done, self.done_at, self.crate_tries, self.cool_for = [], {}, {}, 1e9
                self.round_first = True
                self.cheapest_first()
                while self.at_stop(self.to_game()):  # it stays where the work is: no trip to the bottom first
                    self.check_abort()
                self.stations_on_map()
                self.cool_for = COOL
            elif name == "menu":
                if badge(self.to_game()):
                    self.buy_menu()
            if name == "menu" and self.owed:
                self.owed_crates()
            elif name == "crates":  # pressed on the page: all of it, whatever was bought
                self.crate_tries, self.owed = {}, 0
                self.walk(self.crate_fn)
            self.log(f"{name} phase done in {time.time() - t0:.0f} s;", self.counts, self.mem.progress())
        except Aborted as e:
            self.cool_for = COOL
            self.log(f"{name} phase aborted: {e}")
        finally:
            self.running_auto = False
            self.set_status(busy=None, last_phase=name, last_took=time.time() - t0)

    def run_phased(self, minutes, x_stations, x_menu):
        """Controller: reads the page's commands every second. Auto: stations phase, x_stations s after its start the menu
        phase, x_menu s after that the next stations phase. Manual: only what the page asks for, one phase at a time."""
        end = time.time() + minutes * 60
        c = self.control()
        if x_stations is not None:  # only when given on the command line: otherwise the page's values stay
            c["x_stations"] = x_stations
        if x_menu is not None:
            c["x_menu"] = x_menu
        with open(CONTROL + ".tmp", "w") as f:  # the page shows and edits these
            json.dump(c, f)
        os.replace(CONTROL + ".tmp", CONTROL)
        self.last_id = max([m["id"] for m in c["cmds"]] + [0])  # commands from an earlier run are not replayed
        self.running_auto = False
        threading.Thread(target=self.heartbeat, daemon=True).start()
        nxt, mode, scan_due = ("stations", time.time()), None, True
        try:
            while time.time() < end:
                c = self.control()
                if c["mode"] != mode:
                    mode = c["mode"]
                    self.log("mode:", mode)
                    if mode == "auto":
                        nxt = ("stations", time.time())
                pending = [m for m in c["cmds"] if m["id"] > self.last_id and m["cmd"] != "abort"]
                aborts = [m["id"] for m in c["cmds"] if m["id"] > self.last_id and m["cmd"] == "abort"]
                if aborts:
                    self.last_id = max(aborts)  # nothing is running now
                if pending:
                    m = pending[0]
                    self.last_id = m["id"]
                    self.phase(m["cmd"], False)
                    continue
                if scan_due:  # at the start, also in manual mode: which stations are there, and where
                    scan_due = False
                    self.phase("map", mode == "auto")
                    continue
                if mode == "auto" and time.time() >= nxt[1]:
                    name, start = nxt[0], time.time()
                    self.phase(name, True)
                    if self.control()["mode"] == "auto":
                        nxt = ("menu", start + c["x_stations"]) if name == "stations" else ("stations", start + c["x_menu"])
                self.set_status(mode=mode, next=None if mode != "auto" else {"phase": nxt[0], "at": nxt[1]}, busy=None,
                                x_stations=c["x_stations"], x_menu=c["x_menu"])
                time.sleep(1.0)
        finally:
            self.set_status(busy=None, mode="stopped")
            self.log("totals", self.counts, self.mem.progress(), self.mem.d["cadence"])


def check(paths):
    for p in paths:
        img = cv2.imread(p)
        s = screen(img)
        print(os.path.basename(p), s, "arrows", red_arrows(img) if img is not None else None)
        print("   next active", next_active(img), "dialog button", dialog_button(img))
        if s in ("game", "panel"):
            box = panel(img)
            print("   stations", stations(img), "badge", badge(img), "crates", crates(img), "slots", slots(img))
            print("   button", panel_button(img), "unlock", is_unlock(img, panel_button(img)) if box else None, "floor", floor(img, box))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--clicks", type=int, default=2000, help="at most this many clicks per station turn")
    ap.add_argument("--check", nargs="+", metavar="PNG")
    ap.add_argument("--mode", choices=("phases", "watch"), default="phases")
    ap.add_argument("--x-stations", type=float, default=None, help="phase mode: seconds from the start of one stations phase to the next menu phase")
    ap.add_argument("--x-menu", type=float, default=None, help="phase mode: seconds from the start of a menu phase to the next stations phase")
    a = ap.parse_args()
    if a.check:
        return check(a.check)
    try:
        bot = Bot(a.clicks)
        if a.mode == "phases":
            bot.run_phased(a.minutes, a.x_stations, a.x_menu)
        else:
            bot.run(a.minutes)
    except Stop as e:
        print("stop:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
