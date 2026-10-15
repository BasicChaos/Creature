"""
Air change: the room's air as the weather anchor's sense (15 October 2026).

The weather anchor (C10) is fed the BME280's temperature as a level. A room
holds its temperature to about half a degree, so the anchor is under steady
pressure, starved of energy, and hardly ever surprised. The air sensors on the
body move far more. On 6.3 days of recorded air (4 to 15 October 2026) the
clearest thing in them is fresh air: about three times a day CO2 falls 100 to
200 ppm in ten minutes, and the air temperature and the VOC index fall with it.
A window or a door. Showers and cooking show as humidity and VOC rising.

This turns the air into one number for the field: how far the air stands from
its own recent average, CO2 first. Each reading is compared with a slow average
of itself (about an hour), the distance is scaled by what counts as a large
move for that reading, and the four are mixed:

  CO2           0.5   250 ppm from its average is a large move
  VOC index     0.2   150 index points
  air temp      0.2   1.0 C
  humidity      0.1   6 % RH

Before AIR_GAIN, on the recorded air it read 0.13 at the median and 0.80 at
the 99th percentile: a lived-in room is rarely still, since CO2 climbs while
someone is in it. At the gain of 0.5, 0.065 and 0.40. A reading the body has not sent (None, or the VOC index's first 45
seconds) adds nothing.

The body reads the air every 20 seconds, so the value would move in steps.
The field's low-stimulation sleep needs 160 ticks in a row with no sense moving
0.015 or more in one tick, and about 8 steps an hour were that large (CO2 moving
8 ppm between readings): on the recorded week that cost 3 of 9 sleeps. So the
value ramps toward each new reading over about AIR_RAMP_TICKS instead.

The averages are not saved: after a restart they start again from the first
readings, so the first minutes read near 0.

Off unless CREATURE_AIR_WEATHER=1. With it on, the collector feeds this to the
field as "weather" in place of the temperature level. It does not touch the
field's own WEATHER_INPUT, which should stay "level" with this on, since this
is already a change. The gate is `python tools/field_lab_v06.py --air`.

Plain Python, no numpy.
"""

import os

AIR_WEATHER = os.environ.get("CREATURE_AIR_WEATHER", "0") == "1"

AIR_CHANGE_TICKS = 3600      # the slow averages follow over about an hour
AIR_RAMP_TICKS = 20          # the value eases toward each reading over about this long
# The whole value is scaled by this (CREATURE_AIR_GAIN, or --air-gain in the
# gate). A gentler air leaves C10 more energy and grows its links further, and
# the speaker's link C0-C1 grows more with it, not less: 0.83 at 1.0, 0.97 at
# 0.5, 1.06 at 0.25, against 0.57 on the temperature. 0.5 by the rule agreed on
# 15 October 2026 before the runs: the strongest air that passes every check
# of the --air gate (1.0 fails two, 0.5 and 0.25 pass all eight).
AIR_GAIN = float(os.environ.get("CREATURE_AIR_GAIN", "0.5"))
# (key, weight, a large move)
AIR_PARTS = (
    ("co2_ppm", 0.5, 250.0),
    ("voc_index", 0.2, 150.0),
    ("air_temp_c", 0.2, 1.0),
    ("humidity_pct", 0.1, 6.0),
)


class AirChange:
    def __init__(self, ticks=AIR_CHANGE_TICKS, parts=AIR_PARTS, ramp=AIR_RAMP_TICKS, gain=None):
        self.ticks = ticks
        self.gain = AIR_GAIN if gain is None else gain
        self.parts = parts
        self.ramp = max(1, ramp)
        self.average = {key: None for key, _, _ in parts}
        self.last = 0.0

    def step(self, readings):
        """One tick. `readings` is a dict with any of the keys in AIR_PARTS,
        each a number or None. Returns 0 to 1."""
        value = 0.0
        for key, weight, scale in self.parts:
            reading = readings.get(key)
            if reading is None or (key == "voc_index" and reading <= 0):
                continue
            reading = float(reading)
            if self.average[key] is None:
                self.average[key] = reading
            moved = abs(reading - self.average[key]) / scale
            value += weight * min(1.0, moved)
            self.average[key] += (reading - self.average[key]) / self.ticks
        self.last += (min(1.0, self.gain * value) - self.last) / self.ramp
        return self.last
