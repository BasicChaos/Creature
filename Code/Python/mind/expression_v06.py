"""
Read-only v06 expression decoder.

The ESP body only renders commands. This module maps the twelve-cell v06 field
state into the body protocol:

    PIX:r,g,b,w,...     one RGBW frame for the SK6812 strip
    VOX:freq,ms,vol     optional short speaker tone

The decoder keeps no memory except a travelling pulse phase for the strip. It
does not feed back into the field.
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
        raw_a = 0.48 * mean_live + 0.52 * peak
        arousal = clamp(raw_a * k["ACT_GAIN"] * gate, 0.0, 1.0)
        tempo = clamp((sum(abs(r) for r in ripples) / len(ripples)) / k["RIPPLE_REF"], 0.0, 1.0)

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
        )

        return {
            "A": round(arousal, 4),
            "B": round(balance, 4),
            "T": round(tempo, 4),
            "pixels": pixels,
            "event": event_flag,
        }

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

    def _render(self, activations, warm_by_cell, cool_by_cell, arousal, balance, tempo, event_n, event_sig, tick):
        k = self.knobs
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
            warmth = clamp(0.62 * warmth + 0.38 * balance, -1.0, 1.0)

            mix = (warmth + 1.0) * 0.5
            red = k["COOL"][0] * (1.0 - mix) + k["WARM"][0] * mix
            green = k["COOL"][1] * (1.0 - mix) + k["WARM"][1] * mix
            blue = k["COOL"][2] * (1.0 - mix) + k["WARM"][2] * mix

            value = k["FLOOR"] + (1.0 - k["FLOOR"]) * clamp(local_a * k["ACT_GAIN"] + arousal * 0.22, 0.0, 1.0)
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


def voice_command_from_signal(signal, speaker_activation):
    arousal = max(float(signal.get("A", 0.0) or 0.0), float(speaker_activation or 0.0))
    if arousal < float(os.environ.get("CREATURE_VOICE_THRESHOLD", "0.45")):
        return None

    balance = clamp(float(signal.get("B", 0.0) or 0.0), -1.0, 1.0)
    tempo = clamp(float(signal.get("T", 0.0) or 0.0), 0.0, 1.0)
    k = KNOBS
    mix = (balance + 1.0) * 0.5
    freq = k["F_LOW"] * ((k["F_HIGH"] / k["F_LOW"]) ** mix)
    ms = int(220 + 180 * tempo)
    # MAX98357A gets fuzzy with tiny digital samples. Keep the digital signal in
    # the clean range from the bench notes; use the amp GAIN pin for quiet.
    vol = clamp(float(os.environ.get("CREATURE_VOICE_VOLUME", "0.75")), 0.65, 0.9)
    return f"VOX:{freq:.1f},{ms},{vol:.2f}\n"
