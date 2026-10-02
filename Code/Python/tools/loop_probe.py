"""
Loop probe: measure how much of the creature's own output comes back.

The loop only exists if the light sensor can see the strip and the mic can hear
the speaker. This tool measures both on the real body, with known outputs, so
the answer is a number instead of a hope:

    light   switch the strip between dark and a known frame, read the lux both
            ways, repeat. Reports the lux each frame adds at the sensor and how
            that compares with the sensor's own noise.
    sound   send known tones, compare the mic's peak with the room just before.

It talks to the body directly, so THE COLLECTOR MUST BE STOPPED FIRST: the body
accepts one connection at a time. Stop the collector (Ctrl-C in its tmux
window), run the probe, start the collector again. The field is not involved
and nothing is learned or saved except the report.

Run on the Pi (from Code/Python):

    python tools/loop_probe.py tcp://creature-esp.local:7777
    python tools/loop_probe.py /dev/ttyUSB0 --no-sound

Run it once in daylight and once in the dark. The strip adds the same lux
either way, but the room's own noise is very different.
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from collector.collector import (
    DEFAULT_PORT,
    DEFAULT_TCP_PORT,
    LIGHT_MIN_RANGE,
    STRIP_PIXELS,
    STRIP_VALUE_CAP,
    TransportError,
    open_esp_transport,
    parse_line,
)
from common.paths import DB_DIR, MUTE_FLAG_PATH
from mind.expression_v06 import pixels_to_pix_command

# The BH1750 integrates for ~120 ms, so the first readings after a frame change
# still hold the old frame. Skip them.
SETTLE_SECONDS = 0.4


def frames(cap):
    """The known frames to test, name -> one RGBW pixel. `cap` is the highest
    channel value the collector ever sends."""
    half = cap // 2
    return [
        ("white channel", (0, 0, 0, cap)),
        ("red", (cap, 0, 0, 0)),
        ("green", (0, cap, 0, 0)),
        ("blue", (0, 0, cap, 0)),
        ("all four", (cap, cap, cap, cap)),
        ("all four, half", (half, half, half, half)),
    ]


class Body:
    """The ESP stream, read on the side while commands go out."""

    def __init__(self, target):
        self.transport = open_esp_transport(target)

    def send(self, command):
        self.transport.write(command.encode("utf-8"))

    def set_frame(self, pixel, count):
        self.send(pixels_to_pix_command([pixel] * count))

    def read(self, seconds):
        """Collect samples for `seconds`. Returns [(age, lux, rms)], age counted
        from the start of the read. Failed light reads (-1) come back as None."""
        start = monotonic()
        out = []
        while True:
            now = monotonic()
            if now - start >= seconds:
                return out
            sample = parse_line(self.transport.readline())
            if sample is None:
                continue
            lux = float(sample["light_lux"])
            out.append((now - start, lux if lux >= 0.0 else None, float(sample["sound_rms"])))

    def close(self):
        self.transport.close()


def settled_lux(samples):
    values = [lux for age, lux, _ in samples if lux is not None and age >= SETTLE_SECONDS]
    return values


def probe_light(body, pixels, cap, hold, cycles):
    """Dark, frame, dark, frame... for each test frame. The lux a frame adds is
    its reading minus the mean of the dark readings on either side, which
    cancels a room that is slowly getting lighter or darker."""
    dark = (0, 0, 0, 0)
    results = []
    dark_noise = []
    for name, pixel in frames(cap):
        adds = []
        body.set_frame(dark, pixels)
        before = settled_lux(body.read(hold))
        for _ in range(cycles):
            body.set_frame(pixel, pixels)
            lit = settled_lux(body.read(hold))
            body.set_frame(dark, pixels)
            after = settled_lux(body.read(hold))
            if before and lit and after:
                base = (statistics.mean(before) + statistics.mean(after)) / 2.0
                adds.append(statistics.mean(lit) - base)
                if len(before) > 1:
                    dark_noise.append(statistics.pstdev(before))
            before = after
        if adds:
            results.append({
                "frame": name,
                "pixel": list(pixel),
                "lux_added": round(statistics.mean(adds), 2),
                "spread": round(statistics.pstdev(adds), 2) if len(adds) > 1 else 0.0,
                "cycles": len(adds),
            })
            print(f"    {name:16s} adds {results[-1]['lux_added']:8.2f} lux  "
                  f"(+/- {results[-1]['spread']:.2f} over {len(adds)} cycles)")
        else:
            print(f"    {name:16s} no usable light readings")
    noise = statistics.mean(dark_noise) if dark_noise else None
    return results, noise


def probe_sound(body, tones, volume, ms, gap):
    """For each tone: listen to the room, send the tone, listen again. The body
    stops streaming while it plays, so the tone shows in the first readings
    after it, not during it."""
    results = []
    for freq in tones:
        room = [rms for _, _, rms in body.read(gap)]
        body.send(f"VOX:{freq:.1f},{ms},{volume:.2f}\n")
        heard = [rms for _, _, rms in body.read(ms / 1000.0 + 1.5)]
        if not room or not heard:
            print(f"    {freq:6.0f} Hz  no mic readings")
            continue
        level = statistics.median(room)
        # Judge the tone against the room's own peaks, not its usual level: a
        # room with talking in it peaks far above its median without any tone.
        results.append({
            "freq": freq,
            "room_rms": round(level, 1),
            "room_peak": round(max(room), 1),
            "tone_peak": round(max(heard), 1),
            "times_room": round(max(heard) / level, 1) if level > 0 else None,
            "times_room_peak": round(max(heard) / max(room), 1) if max(room) > 0 else None,
        })
        print(f"    {freq:6.0f} Hz  room {level:9.0f}  room peak {max(room):9.0f}  "
              f"after tone {max(heard):9.0f}  "
              f"({results[-1]['times_room_peak']}x the room's own peak)")
    return results


def main():
    p = argparse.ArgumentParser(description="Measure the creature's light and sound loops.")
    p.add_argument("target", nargs="?",
                   default=os.environ.get("CREATURE_SERIAL_PORT", DEFAULT_PORT),
                   help=f"serial port or tcp://host:{DEFAULT_TCP_PORT}")
    p.add_argument("--no-light", action="store_true", help="skip the strip test")
    p.add_argument("--no-sound", action="store_true", help="skip the tone test")
    p.add_argument("--force-sound", action="store_true",
                   help="play the tones even though the dashboard mute is on")
    p.add_argument("--pixels", type=int, default=STRIP_PIXELS)
    p.add_argument("--cap", type=int, default=STRIP_VALUE_CAP,
                   help="highest channel value to send (default: the collector's cap)")
    p.add_argument("--hold", type=float, default=2.0, help="seconds per frame")
    p.add_argument("--cycles", type=int, default=4, help="dark/lit repeats per frame")
    p.add_argument("--tones", default="220,311,440", help="tone frequencies in Hz")
    p.add_argument("--volume", type=float, default=0.75)
    p.add_argument("--tone-ms", type=int, default=300)
    p.add_argument("--tone-gap", type=float, default=4.0, help="seconds of room before each tone")
    p.add_argument("--json", help="where to save the report (default: next to the database)")
    args = p.parse_args()

    try:
        body = Body(args.target)
    except TransportError as error:
        print(f"Could not open {args.target}: {error}")
        print("Is the collector still running? The body accepts one connection at a time.")
        sys.exit(1)

    report = {"started_at": datetime.now().isoformat(), "target": args.target,
              "cap": args.cap, "pixels": args.pixels}
    print(f"Loop probe on {body.transport.description}")

    try:
        warm = body.read(3.0)
        if not warm:
            print("No samples from the body in 3 seconds. Is the collector still "
                  "holding the connection?")
            sys.exit(1)
        print(f"  {len(warm) / 3.0:.1f} samples per second")

        if not args.no_light:
            room = [lux for _, lux, _ in warm if lux is not None]
            if room:
                print(f"\n  light (room is at {statistics.mean(room):.0f} lux, strip dark = baseline):")
            light, noise = probe_light(body, args.pixels, args.cap, args.hold, args.cycles)
            report["light"] = {"frames": light, "dark_noise_lux": noise,
                               "room_lux": statistics.mean(room) if room else None}
            best = max((f["lux_added"] for f in light), default=0.0)
            if noise is not None:
                print(f"\n    sensor noise with the strip dark: {noise:.2f} lux")
                floor = max(noise, 0.5)
                print(f"    strongest frame adds {best:.1f} lux = {best / floor:.0f}x the noise")
            verdict = ("the sensor sees the strip clearly" if best >= 20.0 else
                       "the sensor sees the strip faintly" if best >= 3.0 else
                       "the sensor does not see the strip")
            print(f"    reading: {verdict}.")
            if best < LIGHT_MIN_RANGE:
                print(f"    note: the field's light sense ignores any change under "
                      f"{LIGHT_MIN_RANGE:.0f} lux, so today the field\n"
                      f"    cannot see this even at full output. The forward model "
                      f"reads raw lux and can.")
            report["light"]["strongest_lux"] = best

        if not args.no_sound:
            if os.path.exists(MUTE_FLAG_PATH) and not args.force_sound:
                print("\n  sound: skipped, the dashboard speaker mute is on "
                      "(--force-sound to play anyway).")
            else:
                print("\n  sound:")
                tones = [float(x) for x in args.tones.split(",") if x.strip()]
                sound = probe_sound(body, tones, args.volume, args.tone_ms, args.tone_gap)
                report["sound"] = {"tones": sound, "volume": args.volume, "ms": args.tone_ms}
                ratios = [t["times_room_peak"] for t in sound if t["times_room_peak"]]
                if ratios:
                    verdict = ("the mic hears the speaker clearly" if min(ratios) >= 3.0 else
                               "the mic hears the speaker faintly" if min(ratios) >= 1.5 else
                               "the tones cannot be told from the room. If the room was "
                               "noisy, run it again when it is quiet")
                    print(f"    reading: {verdict}.")
    finally:
        try:
            body.set_frame((0, 0, 0, 0), args.pixels)
        except TransportError:
            pass
        body.close()

    path = args.json or os.path.join(
        DB_DIR or ".", "loop_probe_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    try:
        with open(path, "w") as out:
            json.dump(report, out, indent=2)
        print(f"\nReport saved to {path}")
    except OSError as error:
        print(f"\nCould not save the report: {error}")
    print("Strip left dark. Start the collector again to bring the creature back.")


if __name__ == "__main__":
    main()
