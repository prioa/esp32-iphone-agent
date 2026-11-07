#!/usr/bin/env python3
"""A schematic map of the current restaurant, built from the live picture, and where the screen is on it. No taps.

usage: python3 -I eatventure_map.py OUT_DIR PNG...     replay pictures, in order, and write the map to OUT_DIR

eatventure_ui.py runs it on live.png. Small tiles of each picture are looked for on the map and vote for the position
(people walking spoil some of them). Only still pictures are painted, a scroll leaves smeared ones, and the game's fixed
icons are left out. map_plan.png is that picture reduced to a few flat colours; stations, crates and empty slots are kept
as items, in pixels of the 720-wide frame with 0 at the top of the map. The bot adds names and levels through
map_marks.jsonl when it opens a station.
"""
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eatventure_bot import NO_TAP, at_end, crates, screen, slots, stations

SCALE = 2  # the map keeps every second pixel
TOP, BOTTOM = 172, 1340  # the rows of a picture that scroll, between the fixed bars
VIEW = (TOP // SCALE, BOTTOM // SCALE)
TILE_W, TILE_H = 50, 50
TILE_XS = range(55, 250, 60)  # between the fixed icons on the left and right edges
TILE_DY = 60
MIN_STD = 12  # a plain tile (lawn, road) fits everywhere
MIN_SCORE = 0.9
MIN_CLEAR = 0.02  # how much better than its second best place a tile must fit
MIN_VOTES = 3  # few, in a crowded kitchen: fits() below still has to agree
MIN_SHARE = 0.35
MIN_FIT = 0.45  # the whole overlap must look like the map: right positions 0.55 and up, wrong ones at most 0.36 (2026-11-06)
MIN_OVERLAP = 60  # map rows
STILL = 1  # at most this many map pixels between two pictures: not scrolling
RESET_AFTER = 1200  # pictures of the game in a row that fit nowhere: the map is out of date. Open panels do not count
SAME_ITEM = 70  # frame pixels: closer than this is the same thing
GONE_AFTER = 3  # still pictures in a row without it: a crate or slot is gone
PLAN_CELL = 8  # map pixels per block of the plan
PLAN_COLOURS = 6
UNKNOWN = (235, 235, 235)


def usable():
    """False where the game's fixed icons lie over the restaurant."""
    m = np.ones((VIEW[1] - VIEW[0], 720 // SCALE), bool)
    for x0, y0, x1, y1 in NO_TAP:
        y0, y1 = max(y0 // SCALE - VIEW[0], 0), max(y1 // SCALE - VIEW[0], 0)
        m[y0:y1, x0 // SCALE:x1 // SCALE] = False
    return m


USABLE = usable()


def locate(view, target):
    """Row of `target` where the top of `view` lies, and the number of votes; (None, votes) if they do not agree."""
    votes = []
    for y in range(0, view.shape[0] - TILE_H, TILE_DY):
        for x in TILE_XS:
            tile = view[y:y + TILE_H, x:x + TILE_W]
            if tile.std() < MIN_STD:
                continue
            band = target[:, x - 3:x + TILE_W + 3]
            if band.shape[0] < TILE_H:
                continue
            res = cv2.matchTemplate(band, tile, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < MIN_SCORE:
                continue
            res[max(loc[1] - 12, 0):loc[1] + 13] = -1  # floor tiles fit everywhere: only a tile with one clear place may vote
            if cv2.minMaxLoc(res)[1] <= score - MIN_CLEAR:
                votes.append(loc[1] - y)
    if not votes:
        return None, 0
    votes = np.array(votes)
    best = max(votes.tolist(), key=lambda v: int(np.count_nonzero(np.abs(votes - v) <= 1)))
    agree = votes[np.abs(votes - best) <= 1]
    if len(agree) < MIN_VOTES or len(agree) < MIN_SHARE * len(votes):
        return None, len(agree)
    return int(np.median(agree)), len(agree)


def fits(view, target, known, pos):
    """Does the view, laid on the map at this position, look like the map? People moving lower it, a wrong place more."""
    top, bottom = max(pos, 0), min(pos + view.shape[0], target.shape[0])
    if bottom - top < MIN_OVERLAP:
        return False
    a = view[top - pos:bottom - pos]
    m = known[top:bottom] & USABLE[top - pos:bottom - pos]
    if np.count_nonzero(m) < 1000:
        return False
    return float(np.corrcoef(a[m], target[top:bottom][m])[0, 1]) >= MIN_FIT


def plan(colour, known):
    """The picture as blocks of a few flat colours, without people and small things."""
    c = colour.copy()
    c[~known] = UNKNOWN
    c = cv2.medianBlur(c, 9)
    h, w = c.shape[:2]
    small = cv2.resize(c, (max(w // PLAN_CELL, 1), max(h // PLAN_CELL, 1)), interpolation=cv2.INTER_AREA)
    px = small.reshape(-1, 3).astype(np.float32)
    k = min(PLAN_COLOURS, len(px))
    cv2.setRNGSeed(1)
    _, labels, centres = cv2.kmeans(px, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 1, cv2.KMEANS_PP_CENTERS)
    flat = centres[labels.flatten()].reshape(small.shape).astype(np.uint8)
    return cv2.resize(flat, (w, h), interpolation=cv2.INTER_NEAREST)


class RestaurantMap:
    def __init__(self, out_dir):
        self.png = os.path.join(out_dir, "map.png")
        self.plan_png = os.path.join(out_dir, "map_plan.png")
        self.meta = os.path.join(out_dir, "map.json")
        self.marks = os.path.join(out_dir, "map_marks.jsonl")
        self.marks_at = os.path.getsize(self.marks) if os.path.exists(self.marks) else 0
        self.prev = None  # the last picture's view, grey
        self.state, self.votes = "start", 0
        self.lost_for, self.saved_at, self.dirty, self.refresh = 0, 0.0, False, False
        self.clear()
        try:
            with open(self.meta) as f:
                m = json.load(f)
            saved = cv2.imread(self.png, cv2.IMREAD_UNCHANGED)
            if saved is not None and saved.shape[2] == 4:
                self.colour, self.known = saved[..., :3], saved[..., 3] > 0
                self.grey = cv2.cvtColor(self.colour, cv2.COLOR_BGR2GRAY)
                self.top_seen, self.bottom_seen, self.items = m["top_seen"], m["bottom_seen"], m["items"]
        except (OSError, ValueError, KeyError):
            pass

    def clear(self):
        self.colour, self.grey, self.known, self.pos = None, None, None, None
        self.top_seen, self.bottom_seen, self.items, self.pending = False, False, [], []

    def reset(self):
        """Start over: a new map from the next still picture."""
        self.read_marks()  # what the bot wrote belongs to the old map
        self.clear()
        self.lost_for, self.prev, self.dirty, self.state = 0, None, True, "new map"
        self.save(True)

    def feed(self, img):
        """Take one picture. Returns the state: covered (a dialog or panel lies over the game), scrolling, here, lost."""
        self.read_marks()
        if screen(img) != "game":
            self.prev, self.state = None, "covered"
            self.save()
            return self.state
        small = cv2.resize(img, (img.shape[1] // SCALE, img.shape[0] // SCALE), interpolation=cv2.INTER_AREA)[VIEW[0]:VIEW[1]]
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        moved = locate(grey, self.prev)[0] if self.prev is not None else None
        still = moved is not None and abs(moved) <= STILL
        self.prev = grey
        if self.grey is None:
            self.state = "scrolling"
            if still:
                self.pos, self.state = 0, "here"
                self.paint(small, grey, 0)
                self.look(img)
                self.save()
            return self.state
        pos, self.votes = locate(grey, self.grey)
        if pos is not None and not fits(grey, self.grey, self.known, pos):
            pos = None
        if pos is None:
            self.lost()
            return self.state
        self.lost_for, self.pos = 0, pos
        self.state = "here" if still else "scrolling"
        if still:
            self.paint(small, grey, pos)
            self.look(img)
        self.save()
        return self.state

    def lost(self):
        """The picture fits nowhere: no position, and a position kept from before would be wrong. If that lasts, the map no
        longer shows this place, say after a renovation, while new counters go up, or in another restaurant: start over."""
        self.state, self.pos = "lost", None
        self.lost_for += 1
        if self.lost_for > RESET_AFTER:
            self.reset()

    def grow(self, top, bottom):
        """Add empty rows above and below the map."""
        shape = self.colour.shape
        self.colour = np.vstack([np.zeros((top, *shape[1:]), np.uint8), self.colour, np.zeros((bottom, *shape[1:]), np.uint8)])
        self.grey = np.vstack([np.zeros((top, shape[1]), np.uint8), self.grey, np.zeros((bottom, shape[1]), np.uint8)])
        self.known = np.vstack([np.zeros((top, shape[1]), bool), self.known, np.zeros((bottom, shape[1]), bool)])
        for item in self.items:
            item["y"] += top * SCALE

    def paint(self, small, grey, pos):
        """Only what is still unknown, and during a scan also what has changed: the positions on the map stay."""
        h, w = grey.shape
        if self.colour is None:
            self.colour = np.zeros((h, w, 3), np.uint8)
            self.grey = np.zeros((h, w), np.uint8)
            self.known = np.zeros((h, w), bool)
        if pos < 0:
            self.grow(-pos, 0)
            pos = self.pos = 0
        if pos + h > self.grey.shape[0]:
            self.grow(0, pos + h - self.grey.shape[0])
        new = USABLE if self.refresh else ~self.known[pos:pos + h] & USABLE
        if new.any():
            self.colour[pos:pos + h][new] = small[new]
            self.grey[pos:pos + h][new] = grey[new]
            self.known[pos:pos + h][new] = True
            self.dirty = True

    def to_map(self, x, y):
        return int(x), int(self.pos * SCALE + y - TOP)

    def near(self, x, y, kinds):
        for item in self.items:
            if item["kind"] in kinds and abs(item["x"] - x) < SAME_ITEM and abs(item["y"] - y) < SAME_ITEM:
                return item
        return None

    def add(self, kind, x, y):
        item = self.near(x, y, (kind,))
        if item:
            item["missed"] = 0
            return item
        item = {"kind": kind, "x": x, "y": y, "missed": 0}
        self.items.append(item)
        self.dirty = True
        return item

    def look(self, img):
        """Stations (seen by their arrows), crates and empty slots in this still picture."""
        self.place_marks()
        for name, up in (("top_seen", True), ("bottom_seen", False)):
            if at_end(img, up) and not getattr(self, name):
                setattr(self, name, True)
                self.dirty = True
        seen = {"crate": crates(img), "slot": slots(img)}
        arrows = [self.to_map(x, y) for x, y in stations(img)]
        lo, hi = self.pos * SCALE, self.pos * SCALE + (BOTTOM - TOP)
        for kind, found in seen.items():
            here = [self.add(kind, *self.to_map(x, y)) for x, y in found]
            for item in [i for i in self.items if i["kind"] == kind and lo + 40 <= i["y"] <= hi - 40 and i not in here]:
                item["missed"] += 1
                if item["missed"] >= GONE_AFTER:
                    self.items.remove(item)
                    self.dirty = True
        slot_at = [(i["x"], i["y"]) for i in self.items if i["kind"] == "slot"]
        for x, y in arrows:
            if not any(abs(x - a) < SAME_ITEM and abs(y - b) < SAME_ITEM for a, b in slot_at):
                self.add("station", x, y)["arrow"] = True
        for item in self.items:
            if item["kind"] == "station" and lo <= item["y"] <= hi:
                item["arrow"] = any(abs(item["x"] - x) < SAME_ITEM and abs(item["y"] - y) < SAME_ITEM for x, y in arrows)

    def read_marks(self):
        """What the bot wrote since the last look: station names and levels, and the ends it reached."""
        try:
            with open(self.marks) as f:
                f.seek(self.marks_at)
                lines = f.readlines()
                self.marks_at = f.tell()
        except OSError:
            return
        for line in lines:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("kind") == "scan":
                self.refresh = bool(m.get("on"))
                self.dirty = True
            elif m.get("kind") == "ends":
                self.top_seen = self.top_seen or bool(m.get("top"))
                self.bottom_seen = self.bottom_seen or bool(m.get("bottom"))
                self.dirty = True
            elif m.get("kind") == "station":
                self.pending.append(m)  # placed at the next still picture: the position may lag behind the bot

    def place_marks(self):
        """Names and levels. A name already on the map keeps its place: only a new one is placed from the bot's tap."""
        for m in self.pending:
            x, y = self.to_map(m["x"], m["y"])
            item = next((i for i in self.items if i["kind"] == "station" and i.get("name") == m.get("name")), None)
            if item is None:
                old = self.near(x, y, ("slot",))
                if old:
                    self.items.remove(old)
                item = self.add("station", x, y)
            item.update(name=m.get("name"), level=m.get("level"), max=bool(m.get("max")))
            if m.get("cost"):
                item["cost"] = m["cost"]
            if m.get("order"):
                item["order"] = m["order"]
            self.items = [i for i in self.items if i is item or i.get("name") != item["name"]]
            self.dirty = True
        self.pending = []

    def info(self):
        return {"state": self.state, "pos": None if self.pos is None else self.pos * SCALE,
                "height": 0 if self.grey is None else self.grey.shape[0] * SCALE, "view": BOTTOM - TOP,
                "top_seen": self.top_seen, "bottom_seen": self.bottom_seen, "votes": self.votes, "items": self.items}

    def save(self, force=False):
        if not self.dirty or not (force or time.time() - self.saved_at > 3):
            return
        self.saved_at, self.dirty = time.time(), False
        if self.colour is None:
            for p in (self.png, self.plan_png, self.meta):
                if os.path.exists(p):
                    os.remove(p)
            return
        for path, img in ((self.png, np.dstack([self.colour, self.known.astype(np.uint8) * 255])),
                          (self.plan_png, plan(self.colour, self.known))):
            tmp = path + ".tmp.png"
            cv2.imwrite(tmp, img)
            os.replace(tmp, path)
        with open(self.meta + ".tmp", "w") as f:
            json.dump(self.info(), f)
        os.replace(self.meta + ".tmp", self.meta)


def main():
    out, paths = sys.argv[1], sys.argv[2:]
    os.makedirs(out, exist_ok=True)
    m = RestaurantMap(out)
    for p in paths:
        img = cv2.imread(p)
        if img is not None:
            m.feed(img)
            print(os.path.basename(p), {k: v for k, v in m.info().items() if k != "items"}, len(m.items))
    m.dirty = True
    m.save(True)


if __name__ == "__main__":
    main()
