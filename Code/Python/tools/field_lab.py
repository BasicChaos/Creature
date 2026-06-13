"""
Field lab: offline replay harness for the cell field.

Runs the field against recorded or synthetic sensor input, without the ESP,
collector, or database. This is how a dynamics change gets judged: run a
control, run the variant, compare the numbers — instead of watching the
dashboard and guessing.

Usage (from Code/Python):

    # synthetic day/night cycle, 20k ticks (~5.5 h of creature time)
    python tools/field_lab.py --scenario day --ticks 20000 --seed 1

    # replay real recorded input from a Pi database copy
    python tools/field_lab.py --db /path/to/creature_raw_light.db --ticks 20000

    # save a control run, then compare a variant against it
    python tools/field_lab.py --scenario day --ticks 20000 --json control.json
    python tools/field_lab.py --scenario day --ticks 20000 --set ETA=0.012 \
        --compare control.json

    # override any cell_field constant for one run (repeatable)
    python tools/field_lab.py --scenario bursts --set STRUCTURAL_DECAY=0.0001

Same seed + same input + same overrides = same result. The field uses only
the `random` module, which the harness seeds.
"""

import argparse
import json
import math
import random
import sqlite3
import statistics
import sys
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind import cell_field as cf

REPORT_EVERY_DEFAULT = 2000


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------

def scenario_inputs(name, ticks, rng):
    """Yield (sound, light) pairs for a named synthetic scenario."""
    if name == "quiet":
        for _ in range(ticks):
            yield (0.02 + 0.02 * rng.random(), 0.02 + 0.01 * rng.random())

    elif name == "bursts":
        # Sound bursts every ~40 ticks, light square wave every 600 ticks.
        for t in range(ticks):
            sound = 0.7 if (t % 40) < 3 else 0.03
            light = 0.6 if (t // 600) % 2 == 0 else 0.05
            yield (sound + 0.01 * rng.random(), light + 0.01 * rng.random())

    elif name == "day":
        # A compressed day: light follows a slow sine "sun", sound is sparse
        # at night and clustered during the day, with random life noise.
        day_len = 7200  # one "day" = 2 h of creature time
        for t in range(ticks):
            phase = (t % day_len) / day_len
            sun = max(0.0, math.sin(phase * 2 * math.pi))
            light = 0.05 + 0.6 * sun + 0.04 * rng.random()
            daytime = sun > 0.25
            burst_chance = 0.035 if daytime else 0.004
            if rng.random() < burst_chance:
                sound = 0.35 + 0.5 * rng.random()
            else:
                sound = 0.02 + 0.03 * rng.random()
            yield (min(1.0, sound), min(1.0, light))

    else:
        raise ValueError(f"Unknown scenario: {name}")


def db_inputs(db_path, ticks, start=None):
    """Replay recorded sound_norm/light_norm from a field_tick_log table."""
    conn = sqlite3.connect(db_path)
    where = ""
    params = []
    if start:
        where = "WHERE logged_at >= ?"
        params.append(start)
    rows = conn.execute(
        f"""
        SELECT sound_norm, light_norm FROM field_tick_log
        {where}
        ORDER BY id ASC LIMIT ?
        """,
        params + [ticks],
    ).fetchall()
    conn.close()
    if not rows:
        raise SystemExit(f"No usable rows in {db_path} (field_tick_log).")
    for sound, light in rows:
        yield (sound or 0.0, light or 0.0)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def live_threshold():
    """Weights at or below the scar/prune floor count as not-live."""
    floor = getattr(cf, "PRUNE_FLOOR", 0.0)
    return max(floor + 0.01, 0.05)


def weight_metrics(field):
    threshold = live_threshold()
    live = {k: w for k, w in field.weights.items() if w > threshold}
    all_w = list(field.weights.values())

    # Differentiation: mean live weight by distance band from the nearest
    # anchor, plus sound-side vs light-side asymmetry. A flat field shows
    # neither; a shaped field shows both.
    near_anchor, far = [], []
    sound_side, light_side = [], []
    for (i, j), w in field.weights.items():
        d_anchor = min(
            cf.grid_distance(i, cf.SOUND_ANCHOR), cf.grid_distance(j, cf.SOUND_ANCHOR),
            cf.grid_distance(i, cf.LIGHT_ANCHOR), cf.grid_distance(j, cf.LIGHT_ANCHOR),
            cf.grid_distance(i, cf.EMITTER_ANCHOR), cf.grid_distance(j, cf.EMITTER_ANCHOR),
        )
        (near_anchor if d_anchor <= 2 else far).append(w)
        mid_col = (cf.COORDS[i][1] + cf.COORDS[j][1]) / 2
        if mid_col <= 4:
            sound_side.append(w)
        elif mid_col >= 8:
            light_side.append(w)

    def mean(xs):
        return statistics.mean(xs) if xs else 0.0

    return {
        "total_links": len(all_w),
        "live_links": len(live),
        "scarred_links": len(all_w) - len(live),
        "weight_mean_live": round(mean(list(live.values())), 5),
        "weight_max": round(max(all_w), 5) if all_w else 0.0,
        "weight_mean_near_anchor": round(mean(near_anchor), 5),
        "weight_mean_far": round(mean(far), 5),
        "weight_mean_sound_side": round(mean(sound_side), 5),
        "weight_mean_light_side": round(mean(light_side), 5),
    }


def cell_metrics(field):
    cells = list(field.cells.values())
    gains = [c.homeo_gain for c in cells]
    avg_acts = [c.avg_activation for c in cells]
    gain_max = getattr(cf, "HOMEO_GAIN_MAX", 4.0)
    railed = sum(1 for g in gains if g >= gain_max * 0.999)
    return {
        "avg_activation_mean": round(statistics.mean(avg_acts), 5),
        "avg_activation_std": round(statistics.pstdev(avg_acts), 5),
        "homeo_gain_mean": round(statistics.mean(gains), 4),
        "homeo_gain_railed": railed,
        "max_activation": round(max(c.activation for c in cells), 5),
        "fatigue_mean": round(statistics.mean(c.fatigue for c in cells), 5),
        "energy_mean": round(statistics.mean(c.energy for c in cells), 5),
    }


def snapshot_metrics(field, sleep_count, emitter_track):
    m = {"tick": field.tick_count}
    m.update(weight_metrics(field))
    m.update(cell_metrics(field))
    m["energy_reserve"] = round(field.energy_reserve, 3)
    m["memory_pressure"] = round(field.memory_pressure, 4)
    m["state_counts"] = field._state_counts()
    m["sleep_count"] = sleep_count
    if emitter_track:
        m["emitter_mean"] = round(statistics.mean(emitter_track), 5)
        m["emitter_max"] = round(max(emitter_track), 5)
    return m


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def apply_overrides(pairs):
    for pair in pairs or []:
        name, _, raw = pair.partition("=")
        if not hasattr(cf, name):
            raise SystemExit(f"cell_field has no constant named {name!r}")
        current = getattr(cf, name)
        value = type(current)(raw) if not isinstance(current, bool) else raw == "True"
        setattr(cf, name, value)
        print(f"override: {name} = {value}")


def run(args):
    random.seed(args.seed)
    rng = random.Random(args.seed + 1)  # input randomness separate from field noise

    apply_overrides(args.set)
    field = cf.build_field()

    if args.db:
        inputs = db_inputs(args.db, args.ticks, args.start)
        source = f"db:{args.db}"
    else:
        inputs = scenario_inputs(args.scenario, args.ticks, rng)
        source = f"scenario:{args.scenario}"

    print(f"field {cf.FIELD_VERSION} | {source} | ticks={args.ticks} seed={args.seed}")
    header = ("tick    live/total  w_live  w_near  w_far   actAvg  railed  "
              "E_res  sleeps  states a/r/d/ds")
    print(header)

    sleep_count = 0
    was_sleeping = False
    emitter_track = []
    timeline = []

    for sound, light in inputs:
        field.step(sound, light)
        emitter_track.append(field.emitter_activation)
        if field.sleep_mode == "sleep" and not was_sleeping:
            sleep_count += 1
        was_sleeping = field.sleep_mode == "sleep"

        if field.tick_count % args.report_every == 0:
            m = snapshot_metrics(field, sleep_count, emitter_track[-args.report_every:])
            timeline.append(m)
            c = m["state_counts"]
            print(f"{m['tick']:7d} {m['live_links']:4d}/{m['total_links']:<6d}"
                  f"{m['weight_mean_live']:7.4f} {m['weight_mean_near_anchor']:7.4f} "
                  f"{m['weight_mean_far']:7.4f} {m['avg_activation_mean']:7.4f} "
                  f"{m['homeo_gain_railed']:5d} {m['energy_reserve']:7.2f} "
                  f"{m['sleep_count']:5d}   {c['active']}/{c['resting']}/"
                  f"{c['dormant']}/{c['deep_sleep']}")

    final = snapshot_metrics(field, sleep_count, emitter_track[-2000:])
    result = {
        "field_version": cf.FIELD_VERSION,
        "source": source,
        "ticks": args.ticks,
        "seed": args.seed,
        "overrides": args.set or [],
        "final": final,
        "timeline": timeline,
        "weights": {f"{i}-{j}": round(w, 5) for (i, j), w in field.weights.items()},
    }

    print("\nfinal:")
    for key, value in final.items():
        print(f"  {key}: {value}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1))
        print(f"\nsaved: {args.json}")

    if args.compare:
        compare(result, json.loads(Path(args.compare).read_text()))

    return result


def compare(current, baseline):
    print(f"\ncompare vs {baseline.get('source')} "
          f"(v{baseline.get('field_version')}, seed {baseline.get('seed')}, "
          f"overrides {baseline.get('overrides')}):")
    cur_f, base_f = current["final"], baseline["final"]
    for key in sorted(set(cur_f) & set(base_f)):
        if key == "state_counts":
            continue
        a, b = base_f[key], cur_f[key]
        if isinstance(a, (int, float)) and a != b:
            print(f"  {key:28s} {a:>10} -> {b:>10}")

    # Weight-map divergence: how differently did structure form?
    wa, wb = baseline.get("weights", {}), current.get("weights", {})
    shared = set(wa) & set(wb)
    if shared:
        diffs = [abs(wa[k] - wb[k]) for k in shared]
        print(f"  weight map: mean |diff| = {statistics.mean(diffs):.5f}, "
              f"max |diff| = {max(diffs):.5f} over {len(shared)} links")


def main():
    p = argparse.ArgumentParser(description="Offline replay harness for the cell field.")
    p.add_argument("--ticks", type=int, default=20000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--scenario", choices=["day", "bursts", "quiet"], default="day")
    p.add_argument("--db", help="replay sound/light from a creature DB copy")
    p.add_argument("--start", help="ISO timestamp: replay DB rows from here")
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="override a cell_field constant for this run")
    p.add_argument("--json", help="save full result (metrics + weight map)")
    p.add_argument("--compare", help="baseline result JSON to diff against")
    p.add_argument("--report-every", type=int, default=REPORT_EVERY_DEFAULT)
    run(p.parse_args())


if __name__ == "__main__":
    main()
