"""
Voice audition: play candidate sounds on the real speaker, to choose by ear.

It sends the body plain VOX commands, one sound at a time with a pause between,
prints the name of each as it plays, and prints what the body heard of itself
at the tone's own pitch next to the room just before.

The body accepts one TCP connection, so stop the collector first.

    python tools/voice_audition.py sweep      the speaker across its range, low to high
    python tools/voice_audition.py shapes     one pitch: beep, pluck, swell, soft, bell
    python tools/voice_audition.py tones      one pitch: pure, then with overtones
    python tools/voice_audition.py one --freq 520 --ms 500 --attack 5 --release 450 --h2 0.4

`sweep` works on any firmware. `shapes` and `tones` need the firmware of
7 October 2026 or later, which takes the tone's rise, its dying away and its
overtones in the VOX command; older firmware plays them all as the plain beep.

It is a calibration tool. It does not change the Creature: what is chosen here
only reaches it once it is put into mind/expression_v06.py.
"""

import argparse
import json
import socket
import time

DEFAULT_VOLUME = 0.7

# One third of an octave apart, from where the small speaker starts to speak.
SWEEP = [300, 378, 476, 600, 756, 952, 1200, 1512, 1905]

# name, ms, attack ms, release ms
SHAPES = [
    ("beep (as it is)", 300, 10, 10),
    ("pluck", 450, 4, 440),
    ("swell", 600, 450, 120),
    ("soft", 400, 120, 250),
    ("bell", 1100, 4, 1090),
]

# name, second overtone, third overtone
TONES = [
    ("pure (as it is)", 0.0, 0.0),
    ("warm: some second", 0.4, 0.0),
    ("bright: second and third", 0.5, 0.35),
    ("hollow: third only", 0.0, 0.5),
]


def play(conn, freq, ms, vol, attack=10, release=10, h2=0.0, h3=0.0, wait=None):
    """Send one sound and return the body's report on it, or None."""
    plain = attack == 10 and release == 10 and h2 == 0.0 and h3 == 0.0
    if plain:
        line = f"VOX:{freq:.1f},{ms},{vol:.2f}\n"
    else:
        line = f"VOX:{freq:.1f},{ms},{vol:.2f},{attack},{release},{h2:.2f},{h3:.2f}\n"
    conn.sendall(line.encode())
    end = time.time() + (wait if wait is not None else ms / 1000.0 + 1.5)
    buf = b""
    report = None
    while time.time() < end:
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            if b'"vox"' in raw:
                try:
                    report = json.loads(raw.decode()).get("vox")
                except (ValueError, UnicodeDecodeError):
                    pass
    return report


def heard(report):
    if not report:
        return "no report from the body"
    room = max(float(report.get("room_heard", 0.0)), 1.0)
    own = float(report.get("heard", 0.0))
    return f"heard {own:8.0f} at its own pitch, the room {room:6.0f} ({own / room:5.1f} times)"


def main():
    p = argparse.ArgumentParser(description="Candidate sounds on the real speaker.")
    p.add_argument("what", choices=["sweep", "shapes", "tones", "one"])
    p.add_argument("--freq", type=float, default=440.0, help="pitch for shapes, tones and one")
    p.add_argument("--ms", type=int, default=400)
    p.add_argument("--vol", type=float, default=DEFAULT_VOLUME, help="0.65 to 0.9 is the amp's clean range")
    p.add_argument("--attack", type=int, default=10)
    p.add_argument("--release", type=int, default=10)
    p.add_argument("--h2", type=float, default=0.0)
    p.add_argument("--h3", type=float, default=0.0)
    p.add_argument("--top", type=float, default=2000.0, help="sweep: go no higher than this")
    p.add_argument("--gap", type=float, default=1.0, help="seconds of quiet between sounds")
    p.add_argument("--host", default="creature-esp.local")
    p.add_argument("--port", type=int, default=7777)
    args = p.parse_args()
    vol = max(0.0, min(0.9, args.vol))

    with socket.create_connection((args.host, args.port), timeout=8) as conn:
        conn.settimeout(0.2)
        time.sleep(0.5)

        def say(name, **kw):
            print(f"  {name:28s}", end="", flush=True)
            report = play(conn, vol=vol, **kw)
            print(heard(report), flush=True)
            time.sleep(args.gap)

        if args.what == "sweep":
            print(f"the speaker across its range, volume {vol:.2f}")
            for freq in SWEEP:
                if freq <= args.top:
                    say(f"{freq} Hz", freq=float(freq), ms=350)
        elif args.what == "shapes":
            print(f"shapes at {args.freq:.0f} Hz, volume {vol:.2f}")
            for name, ms, attack, release in SHAPES:
                say(name, freq=args.freq, ms=ms, attack=attack, release=release,
                    h2=args.h2, h3=args.h3)
        elif args.what == "tones":
            print(f"overtones at {args.freq:.0f} Hz, volume {vol:.2f}")
            for name, h2, h3 in TONES:
                say(name, freq=args.freq, ms=500, attack=20, release=300, h2=h2, h3=h3)
        else:
            say(f"{args.freq:.0f} Hz, {args.ms} ms", freq=args.freq, ms=args.ms,
                attack=args.attack, release=args.release, h2=args.h2, h3=args.h3)


if __name__ == "__main__":
    main()
