"""
Dark-room calm (9 October 2026): the darker the room, the dimmer and slower the
strip.

Josh does not want the strip flashy in a dark room. Without this rule it is at
its most restless there: a quiet dark room is where curiosity lifts the white
channel in bursts, and an event still flashes the opposite hue at full strength.

Two parts.

  How dark the room is. The light sensor sees the room and the strip together,
  and how much of the strip it sees changes whenever the strip is moved (1.5 lux
  at full on 3 October 2026, 411 on 9 October, an inch or two away). So no
  distance is assumed. Two readings of the room are kept and the darker one is
  believed:

    the lowest reading of the last minute. The strip can only add light, so
    this is never under the room's own level. With the strip far from the
    sensor it is enough. With the strip close it is not: the strip's own light
    makes a dark room read as lit (the first run of the --dark gate).

    the reading with the strip's own light taken out. For that the rule learns,
    from how the reading moves from one tick to the next as the frame changes,
    how much of the strip the sensor sees, the whole of it and each quarter.
    The room hardly moves in a second and the strip does, so the room's own
    level does not come into it; what slow drift the two share is taken out
    first. Only how bright each colour channel reads against the others is
    fixed here, because that belongs to the LEDs and the sensor and not to
    where they sit. What is learned is credited a little over, so that in
    doubt the light is the strip's own.

  What a dark room does to the strip. The frame the decoder made, with any
  probe's lift already in it, is scaled down and followed slowly instead of
  shown at once. In a lit room the frame goes out untouched, the very same
  numbers. Coming back is eased over some seconds.

It keeps the last frame shown, the last minute's readings and what it has
learned of the strip, and none of it is saved: after a restart it learns the
strip again as the frames change.
It changes what the strip shows, so what is recorded as emitted has to be the
frame this returns. Plain Python, nothing drawn by chance.
"""

import math
import os
import statistics

# The room's level, in lux: at or under DARK_LUX the room is dark and the rule
# is fully on, at or over LIT_LUX it is off. In between it is judged on a log
# scale, as brightness is seen.
DARK_LUX = float(os.environ.get("CREATURE_CALM_DARK_LUX", "3.0"))
LIT_LUX = float(os.environ.get("CREATURE_CALM_LIT_LUX", "40.0"))
ROOM_WINDOW_TICKS = int(os.environ.get("CREATURE_CALM_WINDOW_TICKS", "60"))
# In a fully dark room: the share of the frame's brightness that is shown, and
# the share of the way toward the new frame the strip moves each tick.
DARK_BRIGHTNESS = float(os.environ.get("CREATURE_CALM_BRIGHTNESS", "0.35"))
DARK_FOLLOW = float(os.environ.get("CREATURE_CALM_FOLLOW", "0.12"))
# Calm lets go this much a tick once the room is lit again.
WAKE_STEP = float(os.environ.get("CREATURE_CALM_WAKE_STEP", "0.05"))
# How bright each channel reads at the sensor against white, from the loop probe
# of 9 October 2026 (62.6, 137.8, 36.5 and 240 lux at the same value).
CHANNEL_LIGHT = (0.26, 0.57, 0.15, 1.0)
# The body's strip scales every value it is sent by its brightness cap and drops
# the remainder (STRIP_MAX_BRIGHTNESS in the firmware, 40 of 255), so a value
# under 7 shows nothing. The light a frame gives is counted in those steps.
BODY_STRIP_CAP = int(os.environ.get("CREATURE_STRIP_BODY_CAP", "40"))
# Learning what the sensor sees of the strip. The strip is taken as SEGMENTS
# equal parts, and the sensor's reading as so much per unit of the whole strip's
# light plus so much more or less for each part. The whole-strip share needs
# little evidence, the parts more (the two SEEN_DOUBT numbers), so a sensor that
# sees the strip evenly is learned fast and one that sees one end is learned
# as the evidence comes in.
SEGMENTS = int(os.environ.get("CREATURE_CALM_SEGMENTS", "4"))
SEEN_DOUBT = (float(os.environ.get("CREATURE_CALM_DOUBT_WHOLE", "1e-3")),
              float(os.environ.get("CREATURE_CALM_DOUBT_PART", "1e-2")))
# What it has seen fades, so a strip that was moved is learned again, but only
# as new frame changes come in: a tick's evidence ages the old by SEEN_FORGET
# when the frame moved by SEEN_MOVE or more, and by less when it moved less. A
# strip that holds still forgets nothing.
SEEN_FORGET = float(os.environ.get("CREATURE_CALM_FORGET", "0.01"))
SEEN_MOVE = float(os.environ.get("CREATURE_CALM_MOVE", "0.01"))
# The room's light drifts (a cloud, dusk) and the strip's follows it, a little
# behind, because the creature senses the room. Both drifts are taken out, at
# this rate a tick, before the two are compared. Without that the collector on
# the scripted body, whose room rises and falls over ten minutes, credited the
# strip with five times its light and went calm in a 54 lux room.
SEEN_DRIFT = float(os.environ.get("CREATURE_CALM_DRIFT", "0.1"))
# What was learned is never exact, and the miss grows with the strip's light.
# When judging the room the strip is credited with this much more than was
# learned: in doubt, the light is the strip's own and the room is dark. With
# none, a creature started in a dark room with the strip at twice the nearness
# of 9 October lets go of the dark now and then; 0.15 and 0.3 both pass the gate.
OWN_MARGIN = float(os.environ.get("CREATURE_CALM_OWN_MARGIN", "0.15"))
# The room with the strip taken out is the middle of this many ticks.
ROOM_RECENT_TICKS = int(os.environ.get("CREATURE_CALM_RECENT_TICKS", "15"))


def darkness(room_lux):
    """0 in a lit room, 1 in a dark one."""
    if room_lux is None:
        return 0.0
    low, high = math.log(DARK_LUX), math.log(LIT_LUX)
    if high <= low:
        return 1.0 if room_lux <= DARK_LUX else 0.0
    t = (math.log(max(room_lux, DARK_LUX)) - low) / (high - low)
    t = max(0.0, min(1.0, t))
    return 1.0 - t * t * (3.0 - 2.0 * t)


def pixel_light(pixels):
    """Each pixel's light as the body shows it and the sensor would weigh it,
    0 to about 2."""
    cap = BODY_STRIP_CAP + 1
    return [sum(k * ((int(c) * cap) >> 8) for k, c in zip(CHANNEL_LIGHT, px)) / float(cap)
            for px in pixels]


def _solve(matrix, vector):
    """Solve a small linear system by elimination. Plain lists."""
    n = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return [0.0] * n
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        for r in range(col + 1, n):
            factor = rows[r][col] / lead
            if factor:
                for c in range(col, n + 1):
                    rows[r][c] -= factor * rows[col][c]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        out[i] = (rows[i][n] - sum(rows[i][c] * out[c] for c in range(i + 1, n))) / rows[i][i]
    return out


def strip_light(pixels):
    """A frame as the rule sees it: the whole strip's light, then each part's."""
    light = pixel_light(pixels)
    parts = max(1, min(SEGMENTS, len(light)))
    edges = [round(i * len(light) / parts) for i in range(parts + 1)]
    return [sum(light)] + [sum(light[edges[i]:edges[i + 1]]) for i in range(parts)]


class DarkCalm:
    """Reads how dark the room is and calms the strip's frames to match."""

    def __init__(self):
        self.readings = []      # this tick's lux readings so far
        self.lows = []          # one level per tick, the last ROOM_WINDOW_TICKS
        self.rooms = []         # the same with the strip taken out, the last ROOM_RECENT_TICKS
        self.room_lux = None    # the room without the strip
        self.calm = 0.0         # 0 in a lit room, 1 in a dark one
        self.shown = None       # the last frame, before rounding
        # What the sensor sees of the strip.
        self.seen = []          # lux per unit of light: the whole strip, then each part
        self.on_strip = None    # the light of the frame the readings were taken under
        self.before = None      # the frame before it, and the level read under that one
        self.miss = 1.0         # the usual size of a tick's miss, in lux
        self.drift = None       # the slow drift of the reading and of the frame
        self.evidence = None    # what it has seen so far: frame changes against each other,
        self.answer = None      # and against the changes in the reading

    def note_lux(self, lux):
        """One raw reading from the light sensor, the strip's light included."""
        if lux is not None and lux >= 0.0:
            self.readings.append(float(lux))

    def _learn(self, level):
        """Add this tick to what is known of how the reading moves with the
        frame, and work out again what the sensor sees."""
        light = self.on_strip
        n = len(light)
        if len(self.seen) != n:
            self.seen = [0.0] * n
            self.evidence = [[0.0] * n for _ in range(n)]
            self.answer = [0.0] * n
            self.drift = [0.0] * (n + 1)
            self.before = None
        if self.before is not None:
            moved = [a - b for a, b in zip(light, self.before[0])]
            rose = level - self.before[1]
            x = [m - d for m, d in zip(moved, self.drift)]
            miss = (rose - self.drift[n]) - sum(w * c for w, c in zip(self.seen, x))
            # A lamp switched on or off is not the strip's doing: a miss far
            # outside the usual teaches no more than a usual one.
            limit = 4.0 * max(self.miss, 0.5)
            self.miss += 0.05 * (min(abs(miss), limit) - self.miss)
            miss = max(-limit, min(limit, miss))
            y = sum(w * c for w, c in zip(self.seen, x)) + miss
            keep = 1.0 - SEEN_FORGET * min(1.0, sum(c * c for c in x) / SEEN_MOVE)
            for i in range(n):
                row = self.evidence[i]
                for j in range(n):
                    row[j] = keep * row[j] + x[i] * x[j]
                self.answer[i] = keep * self.answer[i] + x[i] * y
            doubted = [list(row) for row in self.evidence]
            for i in range(n):
                doubted[i][i] += SEEN_DOUBT[0 if i == 0 else 1]
            self.seen = _solve(doubted, self.answer)
            for i in range(n):
                self.drift[i] += SEEN_DRIFT * (moved[i] - self.drift[i])
            self.drift[n] += SEEN_DRIFT * y      # y is the rise with the old drift taken out
        self.before = (light, level)

    def own_lux(self):
        """Lux the rule credits to the frame now on the strip. The strip only
        adds light."""
        if self.on_strip is None or len(self.seen) != len(self.on_strip):
            return 0.0
        return max(0.0, sum(w * b for w, b in zip(self.seen, self.on_strip)))

    def step(self):
        """Close the tick. Returns calm."""
        if self.readings:
            # The middle reading of the tick, so one bad sample cannot make a
            # lit room look dark for a minute.
            level = statistics.median(self.readings)
            self.readings = []
            self.lows.append(level)
            del self.lows[:-ROOM_WINDOW_TICKS]
            if self.on_strip is not None:
                self._learn(level)
            self.rooms.append(max(0.0, level - (1.0 + OWN_MARGIN) * self.own_lux()))
            del self.rooms[:-ROOM_RECENT_TICKS]
        if self.lows:
            self.room_lux = min(min(self.lows), statistics.median(self.rooms))
            target = darkness(self.room_lux)
            self.calm = target if target >= self.calm else max(target, self.calm - WAKE_STEP)
        return self.calm

    def _show(self, pixels):
        self.on_strip = strip_light(pixels) if pixels else None
        return pixels

    def frame(self, pixels):
        """The frame to show in place of `pixels`. The same object when the room
        is lit."""
        if self.calm <= 0.0 or not pixels:
            self.shown = [tuple(float(c) for c in px) for px in pixels] if pixels else None
            return self._show(pixels)
        scale = 1.0 - self.calm * (1.0 - DARK_BRIGHTNESS)
        follow = 1.0 - self.calm * (1.0 - DARK_FOLLOW)
        target = [tuple(c * scale for c in px) for px in pixels]
        if self.shown is None or len(self.shown) != len(target):
            self.shown = target
        else:
            self.shown = [tuple(s + follow * (c - s) for s, c in zip(was, px))
                          for was, px in zip(self.shown, target)]
        return self._show([tuple(int(round(c)) for c in px) for px in self.shown])

    def snapshot(self):
        return {
            "calm": round(self.calm, 4),
            "room_lux": round(self.room_lux, 2) if self.room_lux is not None else None,
            "own_lux": round(self.own_lux(), 2),
        }
