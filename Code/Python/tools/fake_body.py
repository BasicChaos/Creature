"""
Fake body: a stand-in for the ESP, for testing the collector on the Mac.

It speaks the body's protocol over TCP: ten JSON samples a second, `PIX:` and
`VOX:` accepted, and one `vox` report after each tone, the way the v06.8 firmware
does. What the strip shows comes back as lux and what the speaker plays comes
back at the mic, by amounts you choose, so the loop record, the forward model,
the felt loop, curiosity and the voice can all be run end to end with no hardware.

It is a test tool, not a model of the creature: the numbers are plausible, and
the speaker has the peaks and dip measured on the real one on 2 October 2026.

Run it, then point the collector at it with its own database and snapshot so the
real ones are never touched:

    python tools/fake_body.py --port 7811 --coupling 0,0,0,0 --loud-every 47

    CREATURE_DB_PATH=/tmp/creature-test/test.db \\
    CREATURE_STATE_JSON_PATH=/tmp/creature-test/state.json \\
    python collector/collector.py tcp://127.0.0.1:7811

Two things learned the hard way. The collector stops cleanly on SIGINT, so start
it and stop it from one shell command (`... & PID=$!; sleep 300; kill -INT $PID`)
or it may be interrupted early. And keep the room's mic noise steadier than the
collector's SOUND_MIN_RANGE (500): a wider spread makes the rolling normalizer
read a quiet room as moderately loud once a tone's spike leaves its 20 second
window, which looks like the voice re-triggering itself and is not.
"""

import argparse
import json
import math
import random
import socket
import sys
import time
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind.expression_v06 import tone_level

def fake_air(seconds):
    """Made-up air readings: a slow CO2 swell, and every 90 s a 20 s VOC event
    (the raw count falls when there is more of it), so the gas index and the
    dashboard's history lines have something to show."""
    swell = math.sin(2.0 * math.pi * seconds / 240.0)
    event = 2500.0 if seconds % 90.0 >= 70.0 else 0.0
    return {"co2_ppm": int(735 + 60 * swell), "air_temp_c": round(21.5 + 0.3 * swell, 2),
            "humidity_pct": round(64.0 - 1.5 * swell, 1),
            "voc_raw": int(29400 - event), "nox_raw": 17000}


def fake_battery(seconds):
    """A made-up cell: ten minutes down from 90 % to 30 %, then two minutes back
    up on the charger, so the dashboard's battery tile has something to show."""
    phase = seconds % 720.0
    if phase < 600.0:
        pct, rate = 90.0 - 60.0 * phase / 600.0, -360.0
    else:
        pct, rate = 30.0 + 60.0 * (phase - 600.0) / 120.0, 1800.0
    return {"battery_v": round(3.5 + 0.7 * pct / 100.0, 3), "battery_pct": round(pct, 1),
            "battery_rate": rate}


# How loud the speaker is across its range, relative to its usual echo.
# Up to 440 Hz from 2 October 2026; above that from the sweep of 7 October, a
# third of an octave apart, joined on at 440 Hz.
VOICE_RESPONSE = [(220.0, 0.15), (250.0, 0.20), (290.0, 0.25), (325.0, 1.00),
                  (350.0, 0.90), (370.0, 0.30), (400.0, 1.20), (440.0, 1.00),
                  (476.0, 1.6), (600.0, 1.75), (756.0, 3.4), (952.0, 5.8),
                  (1200.0, 4.2), (1512.0, 4.0), (1905.0, 3.0)]


def vox_tone(line):
    """A VOX line as the tone dict the mind uses, with its level at its own pitch."""
    parts = line[4:].split(",")
    tone = {"freq": float(parts[0]), "ms": int(float(parts[1])),
            "vol": float(parts[2]) if len(parts) > 2 else 1.0}
    if len(parts) > 4:
        tone.update(attack=float(parts[3]), release=float(parts[4]),
                    h2=float(parts[5]) if len(parts) > 5 else 0.0,
                    h3=float(parts[6]) if len(parts) > 6 else 0.0)
    tone["level"] = tone_level(tone)
    return tone


def voice_response(freq):
    points = VOICE_RESPONSE
    if freq <= points[0][0]:
        return points[0][1]
    for (f0, r0), (f1, r1) in zip(points, points[1:]):
        if freq <= f1:
            return r0 + (r1 - r0) * (freq - f0) / (f1 - f0)
    return points[-1][1]


def main():
    p = argparse.ArgumentParser(description="A simulated ESP body for collector tests.")
    p.add_argument("--port", type=int, default=7811)
    p.add_argument("--coupling", default="0,0,0,0",
                   help="lux at the sensor per strip channel at full, as r,g,b,w. "
                        "0,0,0,0 is a sensor that cannot see the strip; "
                        "20,35,10,60 is one that can")
    p.add_argument("--ambient", type=float, default=300.0, help="room light in lux")
    p.add_argument("--echo", type=float, default=15000.0,
                   help="mic level at a tone's own pitch, per unit volume")
    p.add_argument("--room-noise", type=float, default=300.0,
                   help="spread of the room's mic level; keep under 500 (see above)")
    p.add_argument("--loud-every", type=int, default=0,
                   help="three loud seconds every N seconds (0 = a still room)")
    p.add_argument("--silent-after", type=float, default=0.0,
                   help="stop sending this many seconds into the first connection "
                        "while keeping it open, like a body that reset (0 = never)")
    p.add_argument("--old-firmware", action="store_true",
                   help="send no vox report after a tone, like firmware before v06.8")
    p.add_argument("--seed", type=int, default=5)
    args = p.parse_args()

    coupling = [float(x) for x in args.coupling.split(",")]
    rng = random.Random(args.seed)
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(1)
    print(f"fake body on tcp://127.0.0.1:{args.port}", flush=True)

    kept_open = []      # sockets left open on purpose, so no FIN reaches the collector
    connections = 0
    while True:
        conn, _ = server.accept()
        conn.setblocking(False)
        connections += 1
        buf = b""
        frames = [(0.0, [0.0] * 4)]     # (time, channel means) of each frame shown
        start = time.monotonic()
        next_sample = start
        blocked_until = 0.0             # playTone blocks the real body's loop
        spike = 0.0                     # the tone, as the 10 Hz stream catches it
        report = None
        try:
            while True:
                now = time.monotonic()
                if args.silent_after and connections == 1 and now - start > args.silent_after:
                    kept_open.append(conn)
                    conn = None
                    print("went silent", flush=True)
                    break
                try:
                    chunk = conn.recv(4096)
                    if chunk == b"":
                        break
                    buf += chunk
                except BlockingIOError:
                    pass

                while b"\n" in buf and now >= blocked_until:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode().strip()
                    if line.startswith("PIX:"):
                        values = [int(x) for x in line[4:].split(",") if x]
                        pixels = [values[i:i + 4] for i in range(0, len(values) - 3, 4)]
                        if pixels:
                            frames.append((now, [sum(px[c] for px in pixels) / (255.0 * len(pixels))
                                                 for c in range(4)]))
                    elif line.startswith("VOX:"):
                        tone = vox_tone(line)
                        freq, ms, vol = tone["freq"], tone["ms"], tone["vol"]
                        blocked_until = now + (40 + ms + 150) / 1000.0
                        own = (args.echo * voice_response(freq) * vol * tone["level"]
                               * (0.9 + 0.2 * rng.random()))
                        spike = 10.0 * own
                        room_heard = 150.0 + 200.0 * rng.random()
                        if not args.old_firmware:
                            report = {"vox": {
                                "freq": freq, "ms": ms, "vol": vol,
                                "heard": round(own + room_heard, 1),
                                "level": round(own * 1.1 + 5000.0, 1),
                                "room": round(4000.0 + 3000.0 * rng.random(), 1),
                                "room_heard": round(room_heard, 1), "n": 4000}}

                if report and now >= blocked_until:
                    conn.sendall((json.dumps(report) + "\n").encode())
                    report = None

                if now >= next_sample and now >= blocked_until:
                    next_sample = now + 0.1
                    # The BH1750 integrates for ~120 ms: a reading shows the
                    # frame that was on the strip a moment ago.
                    shown = [f for t, f in frames if t <= now - 0.15]
                    u = shown[-1] if shown else frames[0][1]
                    lux = args.ambient + sum(coupling[i] * u[i] for i in range(4)) + rng.gauss(0.0, 0.4)
                    lux = round(lux / 0.83) * 0.83      # its ~1 lux resolution
                    rms = 4000.0 + args.room_noise * rng.random()
                    if args.loud_every and int(now - start) % args.loud_every < 3:
                        rms += 60000.0
                    rms += spike
                    spike = 0.0
                    sample = {"time_ms": int((now - start) * 1000), "light_lux": round(lux, 1),
                              "sound_rms": round(rms, 1),
                              "motion": round(0.42 + 0.01 * rng.random(), 4),
                              "temp_c": 22.5, "pressure_hpa": 1029.9,
                              **fake_air(now - start), **fake_battery(now - start)}
                    conn.sendall((json.dumps(sample) + "\n").encode())
                time.sleep(0.005)
        except (BrokenPipeError, ConnectionResetError):
            pass
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
