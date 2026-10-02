"""
Forward model (v06.7): the creature's prediction of its own echo.

The field acts through two emitters, and the two loop sensors can pick that
action back up: the light sensor can see the strip, the mic can hear the
speaker. Until now nothing compared the two. This module is the comparison:
each tick it predicts what the creature's own output should do to its senses,
checks that against what the senses actually did, and learns from the miss.

    act -> predict -> sense -> compare -> learn

It splits every change in the room into two parts: the part the creature
caused (the prediction) and the part it did not (the error). Covering the strip
with a hand, or switching on a lamp, shows up as error. Its own light, once
learned, does not.

Two small models, both learned online by a delta rule, plain Python, no numpy:

  light  the change in lux from one tick to the next, predicted from the change
         in the strip frame (four weights, one per RGBW channel). Working on
         changes instead of levels removes the room's baseline without the
         adaptive normalizer, and keeps daylight from being mistaken for the
         strip: the room and the strip have to move in the same second to be
         confused.
  sound  how far the mic's peak rises above the room when a tone is sent,
         predicted from the tone's volume (one gain).

Passive: it reads what the body emitted and what the sensors returned. It does
not change the field or the body. Feeding the error back into the ring is a
later step, and gets its own control-versus-variant run first.

This is the runtime home of the logic. `tools/field_lab_v06.py --forward`
imports it, so the offline gate and the live collector share one source.
"""

import json
import os


# --- light -----------------------------------------------------------------
LIGHT_MU = 0.05            # NLMS step size
LIGHT_EPS = 1e-3           # keeps a tiny frame change from taking a huge step
LIGHT_NOISE_INIT = 2.0     # lux; starting guess for the unexplained change
LIGHT_NOISE_FLOOR = 0.5    # lux; BH1750 resolution is 1 lux
LIGHT_NOISE_ALPHA = 0.02
# The room's own slow slope (sunrise, dusk), tracked so a creature that
# brightens as the day does is not taught that it made the morning.
LIGHT_TREND_ALPHA = 0.05
# A lamp switching on is hundreds of lux of error in one tick. It is the world,
# not a bad weight, so the update only ever sees this many noise scales of it.
LIGHT_CLIP = 4.0
# Mean absolute channel change that counts as "I changed my light this tick".
# Skill is only scored on those ticks; the rest is the room talking to itself.
LIGHT_ACTED_THRESHOLD = 0.004

# --- sound -----------------------------------------------------------------
SOUND_MU = 0.1
SOUND_EPS = 1e-3
SOUND_AMBIENT_ALPHA = 0.05  # room level, tracked only while not speaking

SKILL_ALPHA = 0.01          # slow average for the explained fraction
SOUND_SKILL_ALPHA = 0.05    # tones are rare (one per 20 s at most), so average faster


def frame_rgbw(pixels):
    """Reduce one strip frame to the mean of each channel, 0-1. This is the
    creature's record of what it just did with its light. None if no frame."""
    if not pixels:
        return None
    count = len(pixels)
    return tuple(
        sum(pixel[ch] for pixel in pixels) / (255.0 * count) for ch in range(4)
    )


def _explained(err_ema, null_ema):
    """How much of the change the model accounts for, against predicting
    nothing: 1 = all of it, 0 = no better than nothing, below 0 = worse."""
    if null_ema <= 1e-9:
        return 0.0
    return max(-1.0, min(1.0, 1.0 - err_ema / null_ema))


class LightModel:
    """Predicts the change in lux from the change in the strip frame."""

    def __init__(self):
        self.w = [0.0, 0.0, 0.0, 0.0]   # lux per channel at full output
        self.prev_u = None
        self.prev_lux = None
        self.noise = LIGHT_NOISE_INIT
        self.trend = 0.0                # lux per tick the room is drifting by itself
        self.err_ema = 0.0
        self.null_ema = 0.0
        self.acted = 0
        self.last = None

    def forget_previous(self):
        """Drop the last frame and reading, so the next step starts a fresh
        difference instead of spanning a gap."""
        self.prev_u = None
        self.prev_lux = None

    def step(self, u, lux):
        """One tick: `u` is the frame that was on the strip (frame_rgbw), `lux`
        the reading taken while it was. Returns this tick's numbers, or None
        when there is nothing to compare yet."""
        if u is None or lux is None:
            self.forget_previous()
            self.last = None
            return None
        if self.prev_u is None:
            self.prev_u, self.prev_lux = u, lux
            self.last = None
            return None

        du = [u[i] - self.prev_u[i] for i in range(4)]
        dlux = lux - self.prev_lux
        pred = sum(self.w[i] * du[i] for i in range(4))
        err = dlux - pred - self.trend

        bound = LIGHT_CLIP * self.noise
        clipped = max(-bound, min(bound, err))
        self.trend += LIGHT_TREND_ALPHA * clipped
        norm = sum(d * d for d in du)
        step = LIGHT_MU * clipped / (LIGHT_EPS + norm)
        for i in range(4):
            self.w[i] += step * du[i]
        self.noise = max(
            LIGHT_NOISE_FLOOR,
            self.noise + LIGHT_NOISE_ALPHA * (abs(clipped) - self.noise),
        )

        if sum(abs(d) for d in du) / 4.0 >= LIGHT_ACTED_THRESHOLD:
            self.acted += 1
            self.err_ema += SKILL_ALPHA * (abs(err) - self.err_ema)
            self.null_ema += SKILL_ALPHA * (abs(dlux - self.trend) - self.null_ema)

        self.prev_u, self.prev_lux = u, lux
        self.last = {
            "delta": round(dlux, 3),
            "pred": round(pred, 3),
            "err": round(err, 3),
            "strip_lux": round(sum(self.w[i] * u[i] for i in range(4)), 3),
        }
        return self.last

    def explained(self):
        return _explained(self.err_ema, self.null_ema)

    def lux_at_full(self):
        """Predicted lux at the sensor with every channel at full."""
        return sum(self.w)

    def snapshot(self):
        return {
            "weights": [round(x, 3) for x in self.w],
            "lux_at_full": round(self.lux_at_full(), 2),
            "noise": round(self.noise, 3),
            "explained": round(self.explained(), 4),
            "acted": self.acted,
            "last": self.last,
        }

    def to_dict(self):
        return {
            "w": self.w, "noise": self.noise, "trend": self.trend, "err_ema": self.err_ema,
            "null_ema": self.null_ema, "acted": self.acted,
        }

    def load_dict(self, data):
        w = [float(x) for x in data.get("w", [])]
        if len(w) == 4:
            self.w = w
        self.noise = max(LIGHT_NOISE_FLOOR, float(data.get("noise", LIGHT_NOISE_INIT)))
        self.trend = float(data.get("trend", 0.0))
        self.err_ema = float(data.get("err_ema", 0.0))
        self.null_ema = float(data.get("null_ema", 0.0))
        self.acted = int(data.get("acted", 0))


class SoundModel:
    """Predicts how far the mic peak rises above the room when a tone is sent."""

    def __init__(self):
        self.gain = 0.0           # rms above the room per unit of tone volume
        self.ambient = None       # room level (mean rms), learned while silent
        self.quiet_excess = 0.0   # the room's usual peak above its own level
        self.err_ema = 0.0
        self.null_ema = 0.0
        self.voiced = 0
        self.last = None
        self.last_voiced = None

    def step(self, voice, rms_mean, rms_max):
        """One tick: `voice` is the tone that was sent ({"freq","ms","vol"}) or
        None, `rms_mean`/`rms_max` the mic over the same window."""
        if rms_mean is None or rms_max is None:
            self.last = None
            return None
        if self.ambient is None:
            self.ambient = rms_mean
        excess = rms_max - self.ambient

        if not voice:
            # Silent tick: learn the room, and call the rest of it the world.
            self.ambient += SOUND_AMBIENT_ALPHA * (rms_mean - self.ambient)
            pred = self.quiet_excess
            err = excess - pred
            self.quiet_excess += SOUND_AMBIENT_ALPHA * err
            self.last = {"voiced": False, "excess": round(excess, 1),
                         "pred": round(pred, 1), "err": round(err, 1)}
            return self.last

        vol = float(voice.get("vol", 0.0) or 0.0)
        pred = self.quiet_excess + self.gain * vol
        err = excess - pred
        self.gain += SOUND_MU * err * vol / (SOUND_EPS + vol * vol)

        self.voiced += 1
        self.err_ema += SOUND_SKILL_ALPHA * (abs(err) - self.err_ema)
        self.null_ema += SOUND_SKILL_ALPHA * (abs(excess - self.quiet_excess) - self.null_ema)

        self.last = {"voiced": True, "excess": round(excess, 1),
                     "pred": round(pred, 1), "err": round(err, 1)}
        self.last_voiced = dict(self.last, freq=voice.get("freq"), vol=vol)
        return self.last

    def explained(self):
        return _explained(self.err_ema, self.null_ema)

    def snapshot(self):
        return {
            "gain": round(self.gain, 1),
            "ambient": round(self.ambient, 1) if self.ambient is not None else None,
            "explained": round(self.explained(), 4),
            "voiced": self.voiced,
            "last": self.last,
            "last_voiced": self.last_voiced,
        }

    def to_dict(self):
        return {
            "gain": self.gain, "ambient": self.ambient,
            "quiet_excess": self.quiet_excess, "err_ema": self.err_ema,
            "null_ema": self.null_ema, "voiced": self.voiced,
        }

    def load_dict(self, data):
        self.gain = float(data.get("gain", 0.0))
        ambient = data.get("ambient")
        self.ambient = float(ambient) if ambient is not None else None
        self.quiet_excess = float(data.get("quiet_excess", 0.0))
        self.err_ema = float(data.get("err_ema", 0.0))
        self.null_ema = float(data.get("null_ema", 0.0))
        self.voiced = int(data.get("voiced", 0))


class ForwardModel:
    """The live forward model the collector keeps. Passive: it predicts, compares
    and learns, persists across runs, and reports. It does not change the body."""

    def __init__(self):
        self.light = LightModel()
        self.sound = SoundModel()
        self.ticks = 0

    def step(self, action, returned):
        """Compare one emitted action with what came back while it was on the
        body.

        action:   {"rgbw": frame_rgbw(...) or None, "voice": {...} or None}
        returned: {"lux": ..., "rms_mean": ..., "rms_max": ...}, any may be None
        """
        self.ticks += 1
        action = action or {}
        returned = returned or {}
        return {
            "light": self.light.step(action.get("rgbw"), returned.get("lux")),
            "sound": self.sound.step(
                action.get("voice"), returned.get("rms_mean"), returned.get("rms_max")
            ),
        }

    def snapshot(self):
        """Compact summary for the live snapshot / dashboard."""
        return {
            "ticks": self.ticks,
            "light": self.light.snapshot(),
            "sound": self.sound.snapshot(),
        }

    def save(self, path):
        """Atomic JSON write of the learned weights. Never raises into the loop."""
        try:
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as out:
                json.dump({
                    "format": "forward-model-v1",
                    "ticks": self.ticks,
                    "light": self.light.to_dict(),
                    "sound": self.sound.to_dict(),
                }, out)
            os.replace(tmp, str(path))
            return True
        except OSError:
            return False

    def load(self, path):
        """Reload persisted weights if the file exists. Returns True on success.
        A new or unreadable file leaves a fresh model."""
        try:
            with open(str(path)) as src:
                data = json.load(src)
        except (OSError, ValueError):
            return False
        try:
            light, sound = LightModel(), SoundModel()
            light.load_dict(data.get("light") or {})
            sound.load_dict(data.get("sound") or {})
        except (KeyError, ValueError, TypeError):
            return False
        self.light, self.sound = light, sound
        self.ticks = int(data.get("ticks", 0))
        return True
