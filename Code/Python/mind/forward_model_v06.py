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
  sound  how loud its own tone comes back, predicted from the tone's volume
         and pitch. With v06.8 firmware the body listens while it plays and
         reports the mic level at the tone's own pitch, next to the same
         measure of the room just before, so room noise at other pitches does
         not count. A small speaker is far from equally loud across its range
         (the real one peaks near 330 and 400 Hz and dips between), so the
         model keeps one gain per band of pitch. Without the body's report it
         falls back to how far the mic's peak rises above the room in the
         second after a tone.

The model itself only predicts, compares and learns. From v06.9 each step also
returns a `feel` value for each loop, 0 to 1: how strongly the creature just
sensed its own output, weighted up by how wrong it was about it. The collector
passes that to the two loop cells in the ring. A well-predicted echo is felt
faintly, a surprising one strongly, and with no physical loop there is nothing
to feel.

This is the runtime home of the logic. `tools/field_lab_v06.py --forward`
imports it, so the offline gate and the live collector share one source.
"""

import json
import math
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
SOUND_PITCH_LOW = 220.0     # Hz, the bottom of the voice's range
SOUND_PITCH_BINS = 8        # bands across the octave above that
SOUND_BIN_ERR_ALPHA = 0.2   # per-band memory of how wrong the last few tones were

# How a loop is felt. A sensed echo always registers a little; the rest of the
# feeling is the share of it that was not predicted.
FEEL_BASE = 0.3
LIGHT_PROVEN_AT = 0.3       # explained fraction at which its own light is fully felt
FEEL_SCALE_ALPHA = 0.05     # the usual size of an echo, for "how strong was this one"

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


def pitch_bin(freq):
    """Which band of the voice's range a pitch falls in, 0 to SOUND_PITCH_BINS-1."""
    if freq <= SOUND_PITCH_LOW:
        return 0
    return min(SOUND_PITCH_BINS - 1, int(math.log2(freq / SOUND_PITCH_LOW) * SOUND_PITCH_BINS))


def pitch_bin_center(index):
    """The pitch in the middle of a band, in Hz."""
    return SOUND_PITCH_LOW * 2.0 ** ((index + 0.5) / SOUND_PITCH_BINS)


def _feel(strength, error_fraction):
    """How much of its own output the creature feels, 0 to 1: the strength of
    what came back, mostly weighted by how unexpected it was."""
    strength = max(0.0, min(1.0, strength))
    error_fraction = max(0.0, min(1.0, error_fraction))
    return strength * (FEEL_BASE + (1.0 - FEEL_BASE) * error_fraction)


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

        # Felt only as far as it expected to see itself, and only once the
        # model has shown it can predict its own light at all. A blind sensor
        # leaves small random weights behind; without the second condition their
        # noise would trickle into the loop cell as if it were seeing something.
        expected = abs(pred)
        seeing = expected / (expected + 2.0 * self.noise)
        proven = max(0.0, min(1.0, self.explained() / LIGHT_PROVEN_AT))
        feel = proven * _feel(seeing, abs(err) / (expected + self.noise))

        self.prev_u, self.prev_lux = u, lux
        self.last = {
            "delta": round(dlux, 3),
            "pred": round(pred, 3),
            "err": round(err, 3),
            "strip_lux": round(sum(self.w[i] * u[i] for i in range(4)), 3),
            "feel": round(feel, 4),
        }
        return self.last

    def own_lux(self, u):
        """Lux at the sensor that the model attributes to the strip frame `u`,
        once it has shown it can predict its own light. Zero until then, so a
        blind sensor's noise weights never touch the room's reading."""
        if u is None or self.explained() < 0.1:
            return 0.0
        return max(0.0, sum(self.w[i] * u[i] for i in range(4)))

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
        # The pitch path: what the body heard at the tone's own pitch. One gain
        # per band of pitch, since the speaker is not equally loud across them.
        self.pitch_gain = [0.0] * SOUND_PITCH_BINS   # own level per unit volume
        self.pitch_n = [0] * SOUND_PITCH_BINS        # tones heard in each band
        self.pitch_err = [0.0] * SOUND_PITCH_BINS    # recent error fraction in each band
        self.pitch_err_ema = 0.0
        self.pitch_null_ema = 0.0
        self.pitch_voiced = 0
        self.echo_scale = None    # the usual size of its own echo
        self.last = None
        self.last_voiced = None

    def predict_pitch(self, freq, vol):
        """Expected own level for a tone. A band it has never tried borrows the
        average of the bands it has."""
        index = pitch_bin(freq)
        if self.pitch_n[index]:
            return self.pitch_gain[index] * vol
        known = [g for g, n in zip(self.pitch_gain, self.pitch_n) if n]
        return (sum(known) / len(known)) * vol if known else 0.0

    def _step_pitch(self, voice, heard):
        """A tone the body listened to while playing. `heard` is its report."""
        vol = float(voice.get("vol", 0.0) or 0.0)
        freq = float(voice.get("freq", SOUND_PITCH_LOW) or SOUND_PITCH_LOW)
        index = pitch_bin(freq)
        own = float(heard.get("heard", 0.0) or 0.0) - float(heard.get("room_heard", 0.0) or 0.0)
        pred = self.predict_pitch(freq, vol)
        err = own - pred

        size = max(abs(own), abs(pred), 1e-6)
        error_fraction = min(1.0, abs(err) / size)
        if self.echo_scale is None:
            self.echo_scale = size
        feel = _feel(size / max(self.echo_scale, 1e-6), error_fraction)
        self.echo_scale += FEEL_SCALE_ALPHA * (size - self.echo_scale)

        if vol > 0.0:
            # The first tones in a band set its gain outright; after that it
            # follows slowly, so one odd tone does not rewrite it.
            rate = max(SOUND_MU, 1.0 / (self.pitch_n[index] + 1))
            self.pitch_gain[index] += rate * (own / vol - self.pitch_gain[index])
        self.pitch_n[index] += 1
        self.pitch_err[index] += SOUND_BIN_ERR_ALPHA * (error_fraction - self.pitch_err[index])

        self.pitch_voiced += 1
        self.pitch_err_ema += SOUND_SKILL_ALPHA * (abs(err) - self.pitch_err_ema)
        self.pitch_null_ema += SOUND_SKILL_ALPHA * (abs(own) - self.pitch_null_ema)

        self.last = {"voiced": True, "excess": round(own, 1), "pred": round(pred, 1),
                     "err": round(err, 1), "feel": round(feel, 4)}
        self.last_voiced = dict(self.last, freq=freq, vol=vol, mode="pitch")
        return self.last

    def step(self, voice, rms_mean, rms_max, heard=None):
        """One tick: `voice` is the tone that was sent ({"freq","ms","vol"}) or
        None, `rms_mean`/`rms_max` the mic over the same window, `heard` the
        body's own report of the tone if its firmware sends one."""
        if voice and heard:
            return self._step_pitch(voice, heard)
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
        self.last_voiced = dict(self.last, freq=voice.get("freq"), vol=vol, mode="peak")
        return self.last

    def explained(self):
        """Skill on the pitch path once the body has reported any tone, since
        that is the measurement that can tell its voice from the room."""
        if self.pitch_voiced:
            return _explained(self.pitch_err_ema, self.pitch_null_ema)
        return _explained(self.err_ema, self.null_ema)

    def snapshot(self):
        pitch = self.pitch_voiced > 0
        known = [g for g, n in zip(self.pitch_gain, self.pitch_n) if n]
        return {
            "mode": "pitch" if pitch else "peak",
            "gain": round(sum(known) / len(known) if pitch and known else self.gain, 1),
            "bins": [
                {"hz": round(pitch_bin_center(i)), "gain": round(self.pitch_gain[i], 1),
                 "n": self.pitch_n[i], "err": round(self.pitch_err[i], 3)}
                for i in range(SOUND_PITCH_BINS)
            ] if pitch else None,
            "ambient": round(self.ambient, 1) if self.ambient is not None else None,
            "explained": round(self.explained(), 4),
            "voiced": self.pitch_voiced if pitch else self.voiced,
            "last": self.last,
            "last_voiced": self.last_voiced,
        }

    def to_dict(self):
        return {
            "gain": self.gain, "ambient": self.ambient,
            "quiet_excess": self.quiet_excess, "err_ema": self.err_ema,
            "null_ema": self.null_ema, "voiced": self.voiced,
            "pitch_gain": self.pitch_gain, "pitch_n": self.pitch_n,
            "pitch_err": self.pitch_err, "pitch_err_ema": self.pitch_err_ema,
            "pitch_null_ema": self.pitch_null_ema, "pitch_voiced": self.pitch_voiced,
            "echo_scale": self.echo_scale,
        }

    def load_dict(self, data):
        self.gain = float(data.get("gain", 0.0))
        ambient = data.get("ambient")
        self.ambient = float(ambient) if ambient is not None else None
        self.quiet_excess = float(data.get("quiet_excess", 0.0))
        self.err_ema = float(data.get("err_ema", 0.0))
        self.null_ema = float(data.get("null_ema", 0.0))
        self.voiced = int(data.get("voiced", 0))
        # A v06.8 file holds the earlier two-weight pitch fit ("pitch_w") and no
        # bands. There is nothing to carry over: the bands start fresh.
        gains = [float(x) for x in data.get("pitch_gain", [])]
        counts = [int(x) for x in data.get("pitch_n", [])]
        errors = [float(x) for x in data.get("pitch_err", [])]
        if len(gains) == len(counts) == len(errors) == SOUND_PITCH_BINS:
            self.pitch_gain, self.pitch_n, self.pitch_err = gains, counts, errors
            self.pitch_err_ema = float(data.get("pitch_err_ema", 0.0))
            self.pitch_null_ema = float(data.get("pitch_null_ema", 0.0))
            self.pitch_voiced = int(data.get("pitch_voiced", 0))
            scale = data.get("echo_scale")
            self.echo_scale = float(scale) if scale is not None else None


class ForwardModel:
    """The live forward model the collector keeps. Passive: it predicts, compares
    and learns, persists across runs, and reports. It does not change the body."""

    def __init__(self):
        self.light = LightModel()
        self.sound = SoundModel()
        self.ticks = 0
        self._unheard = None   # the last tone sent whose report has not arrived

    def step(self, action, returned):
        """Compare one emitted action with what came back while it was on the
        body.

        action:   {"rgbw": frame_rgbw(...) or None, "voice": {...} or None}
        returned: {"lux": ..., "rms_mean": ..., "rms_max": ..., "vox": ...}, any
                  may be None. "vox" is the body's own report of a tone.

        Returns this tick's light and sound numbers, plus `feel`: how strongly
        each loop was just felt, 0 to 1, for the two loop cells.
        """
        self.ticks += 1
        action = action or {}
        returned = returned or {}
        voice = action.get("voice")
        vox = returned.get("vox")

        # A report can land just after its window closes (a long tone over
        # WiFi). It still belongs to the tone before it: match it by pitch.
        late = None
        if vox and not voice and self._unheard is not None:
            if abs(float(vox.get("freq", 0.0) or 0.0) - self._unheard["freq"]) < 1.0:
                late = self._unheard
        self._unheard = voice if (voice and not vox) else None

        light = self.light.step(action.get("rgbw"), returned.get("lux"))
        sound = self.sound.step(
            late or voice, returned.get("rms_mean"), returned.get("rms_max"),
            vox if (voice or late) else None,
        )
        return {
            "light": light,
            "sound": sound,
            "feel": {
                "voice": (sound or {}).get("feel", 0.0),
                "light": (light or {}).get("feel", 0.0),
            },
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
