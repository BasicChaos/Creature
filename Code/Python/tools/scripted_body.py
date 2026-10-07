"""
Scripted body: the real collector loop on a simulated body and a simulated
clock, so a run comes out the same every time.

tools/fake_body.py is the live stand-in. It runs in real time over TCP, so two
runs never see quite the same samples and cannot be compared line for line. This
one runs inside the collector's own process. Each read of the body moves a
pretend clock on by a tenth of a second, the random streams are seeded, and every
command the collector sends is kept. Same seed, same run.

    python tools/scripted_body.py --seconds 1800 --seed 1 --out run.json

It works in a scratch folder of its own and never touches the real database or
field state.

    python tools/scripted_body.py --twin-check --seed 1

runs it twice, with CREATURE_TWIN=0 and with =1, and compares everything the
Creature did: each command sent to the body, each row of every log table, the
saved field, forward model and autobiography, and the last live snapshot. They
must be identical. The twin watches; it must not change the Creature.

It is a test tool, not a model of the creature. The room is plausible and busy
enough to make the field, the voice, the loop and curiosity all do something.
"""

import argparse
import atexit
import contextlib
import io
import json
import math
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from tools.fake_body import voice_response, vox_tone

SAMPLE_SECONDS = 0.1
COUPLING = (20.0, 35.0, 10.0, 60.0)    # lux at the sensor per strip channel at full
ECHO = 15000.0                         # mic level at a tone's own pitch, per unit volume


class ScriptedBody:
    """The body as the collector's transport sees it, on a clock of its own."""

    description = "scripted body"

    def __init__(self, seconds, seed, loud_every):
        self.rng = random.Random(seed + 1000)
        self.start = 1000.0
        self.now = self.start               # the pretend clock, in seconds
        self.end = self.start + seconds
        self.loud_every = loud_every
        self.frames = [(0.0, [0.0] * 4)]    # (time, channel means) of the last frames shown
        self.blocked_until = 0.0            # a tone blocks the real body's loop
        self.spike = 0.0
        self.report = None
        self.sent = []                      # every command, with the second it went out
        self.stopped = False

    def readline(self):
        if self.stopped:
            return ""
        self.now += SAMPLE_SECONDS
        if self.now >= self.end:
            # Stop the collector the way Ctrl-C does, so it saves and closes.
            self.stopped = True
            signal.raise_signal(signal.SIGINT)
            return ""
        if self.now < self.blocked_until:
            return ""
        if self.report is not None:
            line, self.report = json.dumps(self.report), None
            return line

        t = self.now - self.start
        shown = [f for at, f in self.frames if at <= self.now - 0.15]
        u = shown[-1] if shown else self.frames[0][1]
        # The room: light that rises and falls over ten minutes, and now and then
        # three loud seconds with something moving.
        lux = 300.0 + 250.0 * math.sin(2.0 * math.pi * t / 600.0)
        lux += sum(COUPLING[i] * u[i] for i in range(4)) + self.rng.gauss(0.0, 0.4)
        lux = round(lux / 0.83) * 0.83
        rms = 4000.0 + 300.0 * self.rng.random() + self.spike
        self.spike = 0.0
        motion = 0.42 + 0.01 * self.rng.random()
        if self.loud_every and int(t) % self.loud_every < 3:
            rms += 60000.0
            motion += 0.5
        return json.dumps({
            "time_ms": int(t * 1000), "light_lux": round(lux, 1), "sound_rms": round(rms, 1),
            "motion": round(motion, 4),
            "temp_c": round(22.5 + 2.0 * math.sin(2.0 * math.pi * t / 900.0), 2),
            "pressure_hpa": 1029.9,
        })

    def write(self, payload):
        line = payload.decode().strip()
        self.sent.append([round(self.now - self.start, 1), line])
        if line.startswith("PIX:"):
            values = [int(x) for x in line[4:].split(",") if x]
            pixels = [values[i:i + 4] for i in range(0, len(values) - 3, 4)]
            if pixels:
                self.frames.append((self.now, [sum(px[c] for px in pixels) / (255.0 * len(pixels))
                                               for c in range(4)]))
                del self.frames[:-4]
        elif line.startswith("VOX:"):
            tone = vox_tone(line)
            freq, ms, vol = tone["freq"], tone["ms"], tone["vol"]
            self.blocked_until = self.now + (40 + ms + 150) / 1000.0
            own = ECHO * voice_response(freq) * vol * tone["level"] * (0.9 + 0.2 * self.rng.random())
            self.spike = 10.0 * own
            room_heard = 150.0 + 200.0 * self.rng.random()
            self.report = {"vox": {
                "freq": freq, "ms": ms, "vol": vol,
                "heard": round(own + room_heard, 1),
                "level": round(own * 1.1 + 5000.0, 1),
                "room": round(4000.0 + 3000.0 * self.rng.random(), 1),
                "room_heard": round(room_heard, 1), "n": 4000}}

    def close(self):
        pass


def _without_times(value):
    """Drop the wall-clock fields: they differ between any two runs."""
    if isinstance(value, dict):
        return {k: _without_times(v) for k, v in value.items()
                if k not in ("saved_at", "updated_at", "logged_at")}
    if isinstance(value, list):
        return [_without_times(v) for v in value]
    return value


def _read_json(path):
    try:
        return _without_times(json.loads(Path(path).read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def run(args):
    """One collector run on the scripted body. Returns everything it did."""
    folder = tempfile.mkdtemp(prefix="creature-scripted-")
    # Registered before the collector's own exit hooks, so it runs after them.
    atexit.register(shutil.rmtree, folder, True)
    os.environ["CREATURE_DB_PATH"] = os.path.join(folder, "test.db")
    os.environ["CREATURE_STATE_JSON_PATH"] = os.path.join(folder, "state.json")

    from collector import collector
    from mind.curiosity_v06 import Curiosity

    body = ScriptedBody(args.seconds, args.seed, args.loud_every)
    collector.monotonic = lambda: body.now
    collector.open_esp_transport = lambda target: body
    collector.Curiosity = lambda: Curiosity(seed=args.seed + 7)
    sys.argv = [sys.argv[0], "tcp://scripted:0"]
    random.seed(args.seed)

    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            collector.main()
    except BaseException:
        sys.stderr.write(printed.getvalue()[-2000:])
        raise
    if args.verbose:
        print(printed.getvalue())

    conn = sqlite3.connect(collector.DB_PATH)
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    tables = {}
    for name in names:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({name})") if row[1] != "logged_at"]
        tables[name] = [list(row) for row in conn.execute(
            f"SELECT {', '.join(columns)} FROM {name} ORDER BY id")]
    conn.close()

    snapshot = _read_json(collector.STATE_JSON_PATH) or {}
    twin_block = snapshot.pop("twin", None)
    return {
        "seed": args.seed,
        "seconds": args.seconds,
        "twin_on": collector.ENABLE_TWIN,
        "twin_block": twin_block,
        "twin_log": tables.pop("twin_log", []),
        "twin_file": os.path.exists(collector.TWIN_STATE_PATH),
        "sent": body.sent,
        "tables": tables,
        "files": {
            "field state": _read_json(collector.FIELD_STATE_PATH),
            "forward model": _read_json(collector.FORWARD_MODEL_PATH),
            "autobiography": _read_json(collector.EXPR_MEMORY_PATH),
            "last live snapshot": snapshot,
        },
    }


def twin_check(args):
    """The same run with the twin off and on. Everything the Creature did must match."""
    runs = {}
    with tempfile.TemporaryDirectory() as folder:
        for twin in ("0", "1"):
            out = os.path.join(folder, f"twin{twin}.json")
            done = subprocess.run(
                [sys.executable, __file__, "--seconds", str(args.seconds), "--seed", str(args.seed),
                 "--loud-every", str(args.loud_every), "--out", out],
                env=dict(os.environ, CREATURE_TWIN=twin), capture_output=True, text=True)
            if done.returncode != 0:
                print(done.stdout + done.stderr)
                print(f"the run with CREATURE_TWIN={twin} failed")
                return False
            runs[twin] = json.loads(Path(out).read_text())
    off, on = runs["0"], runs["1"]

    print(f"scripted body | THE CREATURE WITH THE TWIN OFF AND ON | seed={args.seed}, "
          f"{args.seconds} seconds")
    same = []

    def compare(name, a, b, unit):
        ok = a == b
        same.append(ok)
        detail = f"{len(a)} {unit}" if isinstance(a, list) else unit
        if not ok and isinstance(a, list) and isinstance(b, list):
            first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            detail = f"{len(a)} against {len(b)} {unit}, first difference at {first}"
        print(f"  [{'same' if ok else 'DIFFERENT'}] {name}: {detail}")

    tones = sum(1 for _, line in off["sent"] if line.startswith("VOX:"))
    compare("commands sent to the body", off["sent"], on["sent"], f"commands, {tones} of them tones")
    for name in sorted(set(off["tables"]) | set(on["tables"])):
        compare(name, off["tables"].get(name, []), on["tables"].get(name, []), "rows")
    for name in off["files"]:
        compare(name, off["files"][name], on["files"].get(name), "the same")

    block = on["twin_block"] or {}
    twin_ran = (on["twin_on"] and not off["twin_on"] and bool(block) and on["twin_file"]
                and len(on["twin_log"]) > 0 and off["twin_block"] is None and not off["twin_file"])
    print(f"  twin off: no twin block, no twin file, {len(off['twin_log'])} twin_log rows")
    print(f"  twin on:  twin {block.get('age')} ticks old, {len(on['twin_log'])} twin_log rows, "
          f"gaps {block.get('gap')}")
    if not twin_ran:
        print("  the twin did not run as expected in one of the two runs")

    ok = all(same) and twin_ran
    print(f"\n  {'IDENTICAL' if ok else 'NOT IDENTICAL'} ({sum(same)}/{len(same)} parts the same)")
    return ok


def main():
    p = argparse.ArgumentParser(description="The collector on a scripted body and clock.")
    p.add_argument("--seconds", type=int, default=1800, help="length of the run on the pretend clock")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--loud-every", type=int, default=47,
                   help="three loud seconds with motion every N seconds (0 = never)")
    p.add_argument("--out", help="save everything the run did as JSON")
    p.add_argument("--twin-check", action="store_true",
                   help="run with CREATURE_TWIN=0 and =1 and compare everything the Creature did")
    p.add_argument("--verbose", action="store_true", help="print the collector's own output")
    args = p.parse_args()

    if args.twin_check:
        sys.exit(0 if twin_check(args) else 1)

    result = run(args)
    tones = sum(1 for _, line in result["sent"] if line.startswith("VOX:"))
    print(f"scripted run: {args.seconds} seconds, seed {args.seed}, twin "
          f"{'on' if result['twin_on'] else 'off'}: {len(result['sent'])} commands "
          f"({tones} tones), {len(result['tables'].get('field_tick_log', []))} ticks logged")
    if args.out:
        Path(args.out).write_text(json.dumps(result))
        print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
