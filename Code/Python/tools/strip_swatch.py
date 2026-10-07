"""
Strip swatch: put a fixed test frame on the real strip, to choose colours by eye.

The frame is the whole palette at once: the coolest colour on the first pixel,
the warmest on the last, and everything the field's balance can pass through in
between. It stays on the strip until something else is sent.

The body accepts one TCP connection, so stop the collector first.

    python tools/strip_swatch.py now               the palette as it is
    python tools/strip_swatch.py violet            cool to warm by way of violet
    python tools/strip_swatch.py green             cool to warm by way of green
    python tools/strip_swatch.py violet --bright 0.5
    python tools/strip_swatch.py now --plain       without the white glow and W channel
    python tools/strip_swatch.py off

It is a calibration tool. It does not change the Creature: the colours it shows
only reach the Creature once they are put into mind/expression_v06.py.
"""

import argparse
import colorsys
import socket
import sys
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind.expression_v06 import KNOBS, pixels_to_pix_command

PIXELS = 16
VALUE = 0.7        # a usual brightness of a pixel's own colour, before the caps
AROUSAL = 0.30     # usual arousal, for the white glow and the W channel


def blend_rgb(mix):
    """The palette as it is: a straight line from COOL to WARM in RGB."""
    return tuple(c * (1.0 - mix) + w * mix for c, w in zip(KNOBS["COOL"], KNOBS["WARM"]))


def blend_hue(mix, way):
    """From COOL to WARM around the colour wheel, keeping the colour strong.
    `way` is +1 by way of violet and red, -1 by way of green and yellow."""
    cool = colorsys.rgb_to_hsv(*[c / 255.0 for c in KNOBS["COOL"]])
    warm = colorsys.rgb_to_hsv(*[c / 255.0 for c in KNOBS["WARM"]])
    span = (warm[0] - cool[0]) % 1.0 if way > 0 else -((cool[0] - warm[0]) % 1.0)
    hue = (cool[0] + span * mix) % 1.0
    sat = cool[1] + (warm[1] - cool[1]) * mix
    val = cool[2] + (warm[2] - cool[2]) * mix
    return tuple(255.0 * c for c in colorsys.hsv_to_rgb(hue, sat, val))


def frame(name, bright, plain):
    if name == "off":
        return [(0, 0, 0, 0)] * PIXELS
    scale = KNOBS["LED_CAP"] / 255.0 * bright
    glow = 0.0 if plain else AROUSAL * KNOBS["WHITE_GLOW"]
    white = 0.0 if plain else (AROUSAL ** 1.7) * KNOBS["WHITE_CHANNEL"]
    out = []
    for p in range(PIXELS):
        mix = p / (PIXELS - 1)
        if name == "now":
            rgb = blend_rgb(mix)
        else:
            rgb = blend_hue(mix, 1 if name == "violet" else -1)
        out.append(tuple(int(min(255.0, c * VALUE + glow) * scale) for c in rgb)
                   + (int(white * scale),))
    return out


def main():
    p = argparse.ArgumentParser(description="A fixed test frame on the real strip.")
    p.add_argument("name", choices=["now", "violet", "green", "off"])
    p.add_argument("--bright", type=float, default=1.0, help="overall brightness, 0 to 1")
    p.add_argument("--plain", action="store_true", help="leave out the white glow and the W channel")
    p.add_argument("--host", default="creature-esp.local")
    p.add_argument("--port", type=int, default=7777)
    p.add_argument("--print", action="store_true", help="print the frame and send nothing")
    args = p.parse_args()

    pixels = frame(args.name, max(0.0, min(1.0, args.bright)), args.plain)
    if args.print:
        for px in pixels:
            print(px)
        return
    with socket.create_connection((args.host, args.port), timeout=8) as conn:
        conn.sendall(pixels_to_pix_command(pixels).encode())
        conn.settimeout(1.0)
        try:
            conn.recv(4096)      # let the body take the line before the socket closes
        except OSError:
            pass
    print(f"sent: {args.name}, brightness {args.bright:g}" + (", plain" if args.plain else ""))


if __name__ == "__main__":
    main()
