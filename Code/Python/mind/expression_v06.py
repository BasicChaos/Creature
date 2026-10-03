"""
Read-only v06 expression decoder.

The ESP body only renders commands. This module maps the twelve-cell v06 field
state into the body protocol:

    PIX:r,g,b,w,...     one RGBW frame for the SK6812 strip
    VOX:freq,ms,vol     optional short speaker tone

The decoder keeps no memory except a travelling pulse phase for the strip and,
from v06.9, the little the voice needs to know when to speak: its usual level of
arousal, how long since something surprised the field, and whether it has
already spoken on this rise. It does not feed back into the field.
"""

import math
import os


DEFAULT_PIXELS = 16

KNOBS = {
    "ACT_GAIN": 4.0,
    "FLOOR": 0.04,
    # Keep white as a glow, not the whole expression. The SK6812 W channel is
    # efficient enough that high values quickly wash every colour to white.
    "WHITE_GLOW": 24.0,
    "WHITE_CHANNEL": 70.0,
    "RIPPLE_REF": 0.18,
    "PULSE_BASE": 0.02,
    "PULSE_SPEED": 0.22,
    "PULSE_WIDTH": 0.11,
    "PULSE_STRENGTH": 1.0,
    "SHIMMER": 14.0,
    "EVENT_SIG_MIN": 0.55,
    "EVENT_WIDTH": 1.3,
    "COOL": (35, 150, 230),
    "WARM": (255, 135, 35),
    "PULSE_RGB": (255, 240, 210),
    "EVENT_RGB": (255, 255, 255),
    "LED_CAP": 200,
    "F_LOW": 220.0,
    "F_HIGH": 440.0,
    # When the voice speaks (v06.9).
    #   "relative"  arousal stands at least VOICE_MARGIN above its own usual
    #               level, something surprised the field in the last few ticks,
    #               and it has come back down since it last spoke. Never while
    #               the field sleeps or is still settling after waking.
    #   "fixed"     the earlier rule, kept for control runs: arousal at or above
    #               0.45. On the twelve-cell body that is nearly always true, so
    #               the voice ran as a metronome at its minimum spacing.
    "VOICE_MODEL": os.environ.get("CREATURE_VOICE_MODEL", "relative"),
    "VOICE_MARGIN": float(os.environ.get("CREATURE_VOICE_MARGIN", "0.20")),
    "VOICE_USUAL_RATE": 0.02,     # step of the running median of arousal
    "VOICE_EVENT_TICKS": 3,       # how recent "just surprised" is
    "VOICE_WAKE_SETTLE": 30,      # ticks after waking with no voice
    "VOICE_WARMUP": 60,           # ticks after start before "usual" means anything
    # How arousal, tempo and balance are scaled before they reach the strip and
    # the tone.
    #   "fixed"     against fixed references (ACT_GAIN, RIPPLE_REF, balance as
    #               it comes). On the body of October 2026 the field's usual
    #               ripple sits far above RIPPLE_REF, so tempo is at its maximum
    #               all the time, and balance keeps to a narrow warm band.
    #   "relative"  against the field's own usual levels. Tempo's reference rises
    #               with the usual ripple and never drops below RIPPLE_REF, usual
    #               arousal is held at or below AROUSAL_USUAL, and balance is
    #               read as warmer or cooler than usual. A calm field reads the
    #               same under both.
    "EXPRESSION_MODEL": os.environ.get("CREATURE_EXPRESSION_MODEL", "fixed"),
    "USUAL_RATE": 0.002,          # step of the running medians, per tick
    "USUAL_WARM": 2.0,            # after a start the step is this over the ticks so far,
    "USUAL_RATE_MAX": 0.1,        # at most this, until it has fallen to USUAL_RATE
    "TEMPO_HEADROOM": 2.5,        # ripple this many times its usual reads as tempo 1
    "AROUSAL_USUAL": 0.30,        # where usual arousal may sit at most
    "BALANCE_SPREAD_GAIN": 2.5,   # balance this many usual swings from usual reads as 1
    "BALANCE_SPREAD_MIN": 0.08,   # smallest swing treated as real
}


def clamp(value, low, high):
    return max(low, min(high, value))


def smoothstep(value, low, high):
    if high <= low:
        return 0.0
    t = clamp((value - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ring_distance(a, b, count):
    d = abs(a - b)
    return min(d, count - d)


def anchor_weight(cell_n, anchor_n, count):
    if anchor_n is None:
        return 0.0
    d = ring_distance(cell_n, anchor_n, count)
    if d == 0:
        return 1.0
    if d == 1:
        return 0.55
    if d == 2:
        return 0.22
    return 0.0


def interpolate_ring(values, pos):
    count = len(values)
    if count == 0:
        return 0.0
    pos = clamp(pos, 0.0, count - 1)
    i0 = int(math.floor(pos))
    i1 = min(i0 + 1, count - 1)
    f = pos - i0
    return values[i0] * (1.0 - f) + values[i1] * f


class ExpressionDecoderV06:
    """Map a v06 field snapshot to strip pixels and voice parameters."""

    def __init__(self, pixels=DEFAULT_PIXELS, knobs=None):
        self.pixels = int(pixels)
        self.knobs = dict(KNOBS)
        if knobs:
            self.knobs.update(knobs)
        self.pulse_pos = 0.0
        self.voice_usual = None       # running median of arousal
        self.voice_armed = True       # False from a tone until arousal settles again
        self.ticks_since_event = 10 ** 6
        self.reads = 0
        # The relative model's running picture of what is usual.
        self.usual_ripple = None
        self.usual_level = None
        self.usual_balance = None
        self.balance_spread = None
        self.usual_steps = 0

    def _step_usual(self, ripple, level, balance):
        """Move the running medians one step toward this tick. Each is judged
        first and stepped after, as the voice rule does. The first reading
        after a start says little about what is usual, so the step starts
        large and shrinks to USUAL_RATE over the first quarter of an hour."""
        k = self.knobs
        self.usual_steps += 1
        rate = max(k["USUAL_RATE"], min(k["USUAL_RATE_MAX"], k["USUAL_WARM"] / self.usual_steps))
        if self.usual_ripple is None:
            self.usual_ripple = max(ripple, 1e-4)
            self.usual_level = max(level, 1e-3)
            self.usual_balance = balance
            self.balance_spread = 0.0
            return
        self.usual_ripple = max(
            self.usual_ripple * ((1.0 + rate) if ripple > self.usual_ripple else (1.0 - rate)), 1e-4)
        self.usual_level = max(
            self.usual_level * ((1.0 + rate) if level > self.usual_level else (1.0 - rate)), 1e-3)
        self.balance_spread += rate * (abs(balance - self.usual_balance) - self.balance_spread)
        self.usual_balance += rate if balance > self.usual_balance else -rate
        self.usual_balance = clamp(self.usual_balance, -1.0, 1.0)

    def _relative_balance(self, balance):
        """Balance as warmer (+) or cooler (-) than the field's usual."""
        k = self.knobs
        span = max(k["BALANCE_SPREAD_MIN"], k["BALANCE_SPREAD_GAIN"] * self.balance_spread)
        return clamp((balance - self.usual_balance) / span, -1.0, 1.0)

    def _voice(self, state, arousal, balance, tempo):
        """Whether to speak this tick, and with what tone. Called once per read."""
        k = self.knobs
        self.reads += 1
        self.ticks_since_event = 0 if state.get("events") else self.ticks_since_event + 1
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        level = max(arousal, float(speaker or 0.0))

        if self.voice_usual is None:
            self.voice_usual = max(level, 1e-3)
        above = level - self.voice_usual
        # Step the median after judging the tick, as the event rule does.
        rate = k["VOICE_USUAL_RATE"]
        self.voice_usual *= (1.0 + rate) if level > self.voice_usual else (1.0 - rate)
        self.voice_usual = max(self.voice_usual, 1e-3)
        if above < 0.5 * k["VOICE_MARGIN"]:
            self.voice_armed = True

        meta = state.get("metabolism") or {}
        tick = int(state.get("tick", 0) or 0)
        last_sleep = meta.get("last_sleep_tick")
        waking = last_sleep is not None and tick - int(last_sleep) < k["VOICE_WAKE_SETTLE"]
        if meta.get("mode") == "sleep" or waking or self.reads <= k["VOICE_WARMUP"]:
            return None
        if above <= k["VOICE_MARGIN"] or not self.voice_armed:
            return None
        if self.ticks_since_event > k["VOICE_EVENT_TICKS"]:
            return None
        self.voice_armed = False
        return voice_tone(balance, tempo)

    def read(self, state):
        cells = sorted(state.get("cells") or [], key=lambda c: c.get("n", 0))
        count = state.get("cell_count") or len(cells)
        if not cells or count <= 0:
            return {"A": 0.0, "B": 0.0, "T": 0.0, "pixels": [(0, 0, 0, 0)], "event": False}

        by_n = {int(c.get("n", 0)): c for c in cells}
        activations = [float(by_n.get(n, {}).get("activation", 0.0)) for n in range(count)]
        ripples = [float(by_n.get(n, {}).get("ripple", 0.0)) for n in range(count)]

        live = [a for a in activations if a > 0.02]
        mean_live = sum(live) / len(live) if live else 0.0
        peak = sorted(activations)[max(0, int(len(activations) * 0.85) - 1)]

        meta = state.get("metabolism") or {}
        reserve = float(meta.get("energy_reserve", 0.0) or 0.0)
        reserve_max = float(meta.get("energy_reserve_max", 1.0) or 1.0)
        gate = smoothstep(reserve / max(0.001, reserve_max), 0.05, 0.40)

        k = self.knobs
        relative = k["EXPRESSION_MODEL"] == "relative"
        raw_a = 0.48 * mean_live + 0.52 * peak
        level = raw_a * k["ACT_GAIN"] * gate
        ripple = sum(abs(r) for r in ripples) / len(ripples)
        # The voice rule decides when to speak from arousal as it always has.
        voice_arousal = clamp(level, 0.0, 1.0)
        act_scale = 1.0
        if relative and self.usual_ripple is not None:
            act_scale = min(1.0, k["AROUSAL_USUAL"] / self.usual_level)
            tempo_ref = max(k["RIPPLE_REF"], k["TEMPO_HEADROOM"] * self.usual_ripple)
        else:
            tempo_ref = k["RIPPLE_REF"]
        arousal = clamp(level * act_scale, 0.0, 1.0)
        tempo = clamp(ripple / tempo_ref, 0.0, 1.0)

        anchors = state.get("sense_anchors") or {}
        sound_a = anchors.get("sound")
        motion_a = anchors.get("motion")
        light_a = anchors.get("light")
        weather_a = anchors.get("weather")

        warm_by_cell = []
        cool_by_cell = []
        for n in range(count):
            warm = (
                anchor_weight(n, sound_a, count)
                + 0.65 * anchor_weight(n, motion_a, count)
            )
            cool = (
                anchor_weight(n, light_a, count)
                + 0.35 * anchor_weight(n, weather_a, count)
            )
            warm_by_cell.append(warm)
            cool_by_cell.append(cool)

        warm_total = sum(activations[n] * warm_by_cell[n] for n in range(count))
        cool_total = sum(activations[n] * cool_by_cell[n] for n in range(count))
        balance = (warm_total - cool_total) / (warm_total + cool_total + 1e-6)
        centre = None
        if relative:
            raw_balance = balance
            if self.usual_balance is not None:
                centre = self._relative_balance
                balance = centre(raw_balance)
            self._step_usual(ripple, level, raw_balance)

        event_n, event_sig, event_flag = self._event_origin(state, count)
        pixels = self._render(
            activations,
            warm_by_cell,
            cool_by_cell,
            arousal,
            balance,
            tempo,
            event_n,
            event_sig,
            int(state.get("tick", 0) or 0),
            act_scale,
            centre,
        )

        out = {
            "A": round(arousal, 4),
            "B": round(balance, 4),
            "T": round(tempo, 4),
            "pixels": pixels,
            "event": event_flag,
        }
        if self.knobs["VOICE_MODEL"] == "relative":
            # The decision is made here, once per tick, and carried in the
            # signal. voice_params_from_signal reads it instead of re-deciding.
            out["voice"] = self._voice(state, voice_arousal, balance, tempo)
        return out

    def _event_origin(self, state, count):
        threshold = self.knobs["EVENT_SIG_MIN"]
        for event in state.get("events") or []:
            sig = clamp(float(event.get("significance", 0.0) or 0.0), 0.0, 2.0)
            if sig < threshold:
                continue
            top = event.get("cells") or []
            if not top:
                continue
            n = top[0].get("n")
            if n is None:
                continue
            return int(n) % count, sig, True
        return None, 0.0, False

    def _render(self, activations, warm_by_cell, cool_by_cell, arousal, balance, tempo, event_n, event_sig, tick,
                act_scale=1.0, centre=None):
        k = self.knobs
        act_gain = k["ACT_GAIN"] * act_scale
        count = len(activations)
        n_pixels = max(1, self.pixels)
        self.pulse_pos = (self.pulse_pos + k["PULSE_BASE"] + k["PULSE_SPEED"] * tempo) % 1.0
        scale = clamp(float(k["LED_CAP"]) / 255.0, 0.0, 1.0)
        out = []

        for p in range(n_pixels):
            x = p / (n_pixels - 1) if n_pixels > 1 else 0.0
            pos = x * (count - 1)
            local_a = interpolate_ring(activations, pos)
            local_warm = interpolate_ring(warm_by_cell, pos) * max(local_a, 0.02)
            local_cool = interpolate_ring(cool_by_cell, pos) * max(local_a, 0.02)
            warmth = (local_warm - local_cool) / (local_warm + local_cool + 1e-6)
            if centre is not None:
                warmth = centre(warmth)
            warmth = clamp(0.62 * warmth + 0.38 * balance, -1.0, 1.0)

            mix = (warmth + 1.0) * 0.5
            red = k["COOL"][0] * (1.0 - mix) + k["WARM"][0] * mix
            green = k["COOL"][1] * (1.0 - mix) + k["WARM"][1] * mix
            blue = k["COOL"][2] * (1.0 - mix) + k["WARM"][2] * mix

            value = k["FLOOR"] + (1.0 - k["FLOOR"]) * clamp(local_a * act_gain + arousal * 0.22, 0.0, 1.0)
            red *= value
            green *= value
            blue *= value

            glow = arousal * k["WHITE_GLOW"]
            red += glow
            green += glow
            blue += glow

            d = min(abs(x - self.pulse_pos), abs(x - self.pulse_pos + 1.0), abs(x - self.pulse_pos - 1.0))
            pulse = math.exp(-((d / k["PULSE_WIDTH"]) ** 2)) * tempo * k["PULSE_STRENGTH"]
            red += pulse * k["PULSE_RGB"][0]
            green += pulse * k["PULSE_RGB"][1]
            blue += pulse * k["PULSE_RGB"][2]

            shimmer = math.sin(tick * 0.73 + p * 2.31) * k["SHIMMER"] * tempo
            red += shimmer
            green += shimmer
            blue += shimmer

            if event_n is not None:
                event_flash = math.exp(-((pos - event_n) / k["EVENT_WIDTH"]) ** 2) * event_sig
                red += event_flash * k["EVENT_RGB"][0]
                green += event_flash * k["EVENT_RGB"][1]
                blue += event_flash * k["EVENT_RGB"][2]

            white = clamp((arousal ** 1.7) * k["WHITE_CHANNEL"], 0.0, 255.0)
            out.append((
                int(clamp(red, 0.0, 255.0) * scale),
                int(clamp(green, 0.0, 255.0) * scale),
                int(clamp(blue, 0.0, 255.0) * scale),
                int(white * scale),
            ))

        return out


def lift_white(pixels, level, cap):
    """Add a probe's lift to the white channel of every pixel. `level` is the
    share of the channel cap to add (0-1). Returns a new frame; white never goes
    past the cap. The decoder stays read-only: this is the one place something
    other than the field's own state reaches the strip."""
    add = int(round(clamp(level, 0.0, 1.0) * cap))
    if add <= 0:
        return pixels
    return [(r, g, b, int(min(cap, w + add))) for r, g, b, w in pixels]


def pixels_to_pix_command(pixels):
    values = []
    for red, green, blue, white in pixels:
        values.extend([
            str(int(clamp(red, 0, 255))),
            str(int(clamp(green, 0, 255))),
            str(int(clamp(blue, 0, 255))),
            str(int(clamp(white, 0, 255))),
        ])
    return "PIX:" + ",".join(values) + "\n"


def voice_tone(balance, tempo):
    """The tone for a given expression: pitch from balance, length from tempo."""
    balance = clamp(float(balance or 0.0), -1.0, 1.0)
    tempo = clamp(float(tempo or 0.0), 0.0, 1.0)
    k = KNOBS
    mix = (balance + 1.0) * 0.5
    freq = k["F_LOW"] * ((k["F_HIGH"] / k["F_LOW"]) ** mix)
    ms = int(220 + 180 * tempo)
    # MAX98357A gets fuzzy with tiny digital samples. Keep the digital signal in
    # the clean range from the bench notes; use the amp GAIN pin for quiet.
    vol = clamp(float(os.environ.get("CREATURE_VOICE_VOLUME", "0.75")), 0.65, 0.9)
    return {"freq": round(freq, 1), "ms": ms, "vol": round(vol, 2)}


def voice_params_from_signal(signal, speaker_activation):
    """The tone the body would voice this tick as {"freq","ms","vol"}, or None
    when it stays silent. The forward model records these as what was emitted.

    A signal from the decoder under the relative rule already carries the
    decision. Anything else (the fixed rule, or a signal built by hand) falls
    back to the fixed threshold on arousal."""
    if "voice" in signal:
        return signal["voice"]
    arousal = max(float(signal.get("A", 0.0) or 0.0), float(speaker_activation or 0.0))
    if arousal < float(os.environ.get("CREATURE_VOICE_THRESHOLD", "0.45")):
        return None
    return voice_tone(signal.get("B", 0.0), signal.get("T", 0.0))


def voice_command_from_signal(signal, speaker_activation):
    params = voice_params_from_signal(signal, speaker_activation)
    if params is None:
        return None
    return f"VOX:{params['freq']:.1f},{params['ms']},{params['vol']:.2f}\n"
