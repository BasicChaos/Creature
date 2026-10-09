"""
Field lab v06: offline replay harness for the twelve-cell ring.

Same idea as tools/field_lab.py but for cell_field_v06: run a control, run a
variant, compare the numbers instead of watching the dashboard and guessing.
The difference is the body (a ring, four senses) and the gate.

The v06 build step 1 gate (Creature v06.md): the ring runs, the two correlated
cells (Sound x Motion, Light x Weather) and the two loop cells (Speaker x Sound,
LED x Light) carry above-baseline weight, and that structure survives a
simulated night. This harness measures exactly those.

Usage (from Code/Python):

    # one compressed day/night, save it as the control
    python tools/field_lab_v06.py --scenario day --ticks 20000 --seed 1 \
        --json control_v06.json

    # a variant, compared against the control
    python tools/field_lab_v06.py --scenario day --ticks 20000 --seed 1 \
        --set ETA=0.012 --compare control_v06.json

    # the night-survival gate: run a day, then hours of quiet, check structure held
    python tools/field_lab_v06.py --gate --seed 1

Same seed + same input + same overrides = same result. The field uses only the
`random` module, which the harness seeds.
"""

import argparse
import colorsys
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind import cell_field_v06 as cf
from mind import forward_model_v06 as fm
from mind.expression_v06 import (
    ExpressionDecoderV06,
    lift_white,
    voice_command_from_signal,
    voice_params_from_signal,
)
# The forward model and the curiosity drive also live in the runtime modules,
# for the same reason.
from mind.forward_model_v06 import (
    ForwardModel,
    frame_rgbw,
    pitch_bin,
    pitch_bin_center,
    SOUND_PITCH_BINS,
)
from mind.curiosity_v06 import Curiosity
# The dark-room rule the collector applies to each frame when CREATURE_DARK_CALM=1.
from mind import calm_v06
from mind.calm_v06 import DarkCalm
# The newborn twin the collector runs beside the real field.
from mind.twin_v06 import Twin
# The expression gate closes the light loop through the collector's normalizer.
from mind.normalize import RollingNormalizer
# Expression-memory primitives live in the runtime module; field_lab imports them
# so the offline gates and the live collector share one source of truth.
from mind.expression_memory_v06 import (
    clamp01,
    expression_vector,
    quantize_expression,
    dequantize_expression,
    ExpressionGraph,
    graph_distance,
    novelty_target as _novelty_target,
)

REPORT_EVERY_DEFAULT = 2000


# ---------------------------------------------------------------------------
# Input sources (four senses now)
# ---------------------------------------------------------------------------

def scenario_inputs(name, ticks, rng):
    """Yield dicts {sound, light, motion, weather} for a named scenario.

    Design choices baked in (Creature v06.md):
      * motion correlates with sound (they share a dedicated cell)
      * weather is slow and stays nonzero at night (the field's night anchor)
      * light follows a day/night sun; sound/motion cluster in the day
    """
    day_len = 7200  # one "day" = 2 h of creature time at 1 Hz

    if name == "quiet":
        for t in range(ticks):
            weather = 0.45 + 0.05 * math.sin(t / day_len * 2 * math.pi)
            yield {
                "sound": 0.02 + 0.02 * rng.random(),
                "light": 0.02 + 0.01 * rng.random(),
                "motion": 0.02 + 0.02 * rng.random(),
                "weather": weather,
            }

    elif name == "bursts":
        for t in range(ticks):
            event = (t % 40) < 3
            sound = 0.7 if event else 0.03
            # motion rides the same events as sound, with its own jitter
            motion = (0.6 if event else 0.04) + 0.02 * rng.random()
            light = 0.6 if (t // 600) % 2 == 0 else 0.05
            weather = 0.5 + 0.08 * math.sin(t / day_len * 2 * math.pi)
            yield {
                "sound": min(1.0, sound + 0.01 * rng.random()),
                "light": min(1.0, light + 0.01 * rng.random()),
                "motion": min(1.0, motion),
                "weather": weather,
            }

    elif name == "day":
        for t in range(ticks):
            phase = (t % day_len) / day_len
            sun = max(0.0, math.sin(phase * 2 * math.pi))
            light = 0.05 + 0.6 * sun + 0.04 * rng.random()
            daytime = sun > 0.25
            burst_chance = 0.035 if daytime else 0.004
            if rng.random() < burst_chance:
                sound = 0.35 + 0.5 * rng.random()
                # motion correlated with sound: most sound events move too
                motion = sound * (0.6 + 0.4 * rng.random()) if rng.random() < 0.8 else 0.04
            else:
                sound = 0.02 + 0.03 * rng.random()
                # occasional motion without sound (something passes silently)
                motion = 0.3 + 0.3 * rng.random() if rng.random() < 0.006 else 0.02 + 0.03 * rng.random()
            # weather: slow daily swing that never reaches zero at night
            weather = 0.45 + 0.18 * math.sin(phase * 2 * math.pi) + 0.02 * rng.random()
            yield {
                "sound": min(1.0, sound),
                "light": min(1.0, light),
                "motion": min(1.0, motion),
                "weather": min(1.0, weather),
            }

    elif name == "steady":
        # A statistically steady environment: each sense a different constant
        # with only tiny noise. Nothing changes, nothing surprises. This is the
        # input that homogenizes a leaky field. A predictive field should stay
        # differentiated; a leaky one flattens to a uniform wash.
        for _ in range(ticks):
            yield {
                "sound": 0.30 + 0.01 * rng.random(),
                "light": 0.55 + 0.01 * rng.random(),
                "motion": 0.25 + 0.01 * rng.random(),
                "weather": 0.50 + 0.01 * rng.random(),
            }

    else:
        raise ValueError(f"Unknown scenario: {name}")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def incident_weight(field, n):
    """Mean weight of the two ring edges touching cell n."""
    ws = [w for (i, j), w in field.weights.items() if i == n or j == n]
    return statistics.mean(ws) if ws else 0.0


def ring_metrics(field):
    """Per-cell incident weight and the named-cell groups for the gate."""
    per_cell = {n: round(incident_weight(field, n), 5) for n in field.cells}
    all_w = list(field.weights.values())
    live = [w for w in all_w if w > cf.LIVE_LINK_THRESHOLD]

    def group_mean(cells):
        return round(statistics.mean(incident_weight(field, n) for n in cells), 5)

    overall = round(statistics.mean(per_cell.values()), 5)
    loop = group_mean(cf.LOOP_CELLS)
    correlated = group_mean(cf.CORRELATED_CELLS)
    weak_gap = round(incident_weight(field, cf.WEAK_GAP_CELL), 5)
    # The design gives only one in-between cell that is meant to be weak (the
    # Weather x Speaker gap). So the honest baseline for "this cell carries
    # learned structure" is the weak-gap cell itself, plus the 0.20 start
    # weight every link begins at. Differentiation is the spread of incident
    # weight across the ring: a flat (homogenized) field has near-zero spread,
    # a shaped one has real spread. This is the entropy number.
    differentiation = round(statistics.pstdev(per_cell.values()), 5)

    return {
        "total_links": len(all_w),
        "live_links": len(live),
        "weight_mean_all": overall,
        "weight_max": round(max(all_w), 5) if all_w else 0.0,
        "differentiation": differentiation,
        "loop_cells_mean": loop,
        "correlated_cells_mean": correlated,
        "weak_gap_mean": weak_gap,
        "per_cell_incident": per_cell,
    }


def cell_metrics(field):
    cells = list(field.cells.values())
    gains = [c.homeo_gain for c in cells]
    avg_acts = [c.avg_activation for c in cells]
    railed = sum(1 for g in gains if g >= cf.HOMEO_GAIN_MAX * 0.999)
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
    m.update(ring_metrics(field))
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
            raise SystemExit(f"cell_field_v06 has no constant named {name!r}")
        current = getattr(cf, name)
        value = type(current)(raw) if not isinstance(current, bool) else raw == "True"
        setattr(cf, name, value)
        print(f"override: {name} = {value}")


def drive(field, inputs, report_every):
    """Step the field across an input iterable, returning (timeline, sleeps)."""
    sleep_count = 0
    was_sleeping = False
    emitter_track = []
    timeline = []
    for values in inputs:
        field.step(values)
        emitter_track.append(field.emitter_activation)
        if field.sleep_mode == "sleep" and not was_sleeping:
            sleep_count += 1
        was_sleeping = field.sleep_mode == "sleep"
        if report_every and field.tick_count % report_every == 0:
            m = snapshot_metrics(field, sleep_count, emitter_track[-report_every:])
            timeline.append(m)
            c = m["state_counts"]
            print(f"{m['tick']:7d}  live {m['live_links']:2d}/{m['total_links']:<2d}  "
                  f"loop {m['loop_cells_mean']:.4f}  corr {m['correlated_cells_mean']:.4f}  "
                  f"gap {m['weak_gap_mean']:.4f}  diff {m['differentiation']:.4f}  "
                  f"E {m['energy_reserve']:.2f}  slp {m['sleep_count']:<3d} "
                  f"a/r/d/ds {c['active']}/{c['resting']}/{c['dormant']}/{c['deep_sleep']}")
    return timeline, sleep_count, emitter_track


def run(args):
    random.seed(args.seed)
    rng = random.Random(args.seed + 1)
    apply_overrides(args.set)
    field = cf.build_field()

    inputs = scenario_inputs(args.scenario, args.ticks, rng)
    source = f"scenario:{args.scenario}"
    print(f"field {cf.FIELD_VERSION} | {source} | ticks={args.ticks} seed={args.seed}")
    print("   tick   live      loop      corr      gap      diff    E   sleeps  states")

    timeline, sleep_count, emitter_track = drive(field, inputs, args.report_every)
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

    print_final(final)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1))
        print(f"\nsaved: {args.json}")
    if args.compare:
        compare(result, json.loads(Path(args.compare).read_text()))
    return result


def print_final(final):
    print("\nfinal:")
    for key, value in final.items():
        if key == "per_cell_incident":
            continue
        print(f"  {key}: {value}")


SPECIAL_NAMES = {
    1: "loop: speaker x sound",
    7: "loop: led x light",
    3: "correlated: sound x motion",
    9: "correlated: light x weather",
}
INITIAL_WEIGHT = cf.CONNECTION_WEIGHT_BY_DISTANCE[1]  # 0.20, every link's start


def gate(args):
    """The step-1 acceptance gate: day, then a quiet night, check survival.

    Honest baseline: a special cell "carries learned structure" if its incident
    weight beats both the weak-gap cell (the one cell the design wants weak) and
    a small margin over the scar floor. Night survival is measured as the
    field's differentiation (spread of incident weight) holding, not collapsing
    to a uniform wash.
    """
    random.seed(args.seed)
    rng = random.Random(args.seed + 1)
    apply_overrides(args.set)
    field = cf.build_field()

    print(f"field {cf.FIELD_VERSION} | GATE | seed={args.seed} | "
          f"day={args.ticks} then quiet night={args.night}")
    print("   tick   live      loop      corr      gap      diff    E   sleeps  states")

    drive(field, scenario_inputs("day", args.ticks, rng), args.report_every)
    day = ring_metrics(field)
    drive(field, scenario_inputs("quiet", args.night, rng), args.report_every)
    night = ring_metrics(field)

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    floor_margin = cf.PRUNE_FLOOR + 0.02  # clearly above the scar floor
    gap = day["weak_gap_mean"]

    # 1. ring runs: field is alive (not dead-zero) and differentiated, not flat.
    check("ring runs and differentiates",
          day["differentiation"] > 0.03 and day["weight_max"] > floor_margin,
          f"differentiation {day['differentiation']:.4f}, max weight {day['weight_max']:.4f}")

    # 2. each loop and correlated cell carries learned structure (end of day),
    #    beating the weak gap and standing clear of the scar floor.
    for n in (1, 7, 3, 9):
        w = day["per_cell_incident"][n]
        check(f"{SPECIAL_NAMES[n]} carries structure",
              w > gap and w > floor_margin,
              f"{w:.4f} (gap {gap:.4f}, floor+ {floor_margin:.2f})")

    # 3. weak gap is genuinely the weakest of the special cells.
    specials = [day["per_cell_incident"][n] for n in (1, 7, 3, 9)]
    check("weak gap is the weakest special cell",
          gap <= min(specials),
          f"gap {gap:.4f} vs min special {min(specials):.4f}")

    # 4. structure survives the night: differentiation holds (>= 60% of day's),
    #    field did not homogenize to a flat wash.
    ratio = night["differentiation"] / day["differentiation"] if day["differentiation"] else 0.0
    check("structure survives the night",
          ratio >= 0.60,
          f"differentiation {day['differentiation']:.4f} -> {night['differentiation']:.4f} "
          f"({ratio*100:.0f}% held)")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")

    print("\n  per-cell incident weight  (end of day -> end of night):")
    for n, (label, _t, _h) in enumerate(cf.RING):
        tag = ("  <- " + SPECIAL_NAMES[n].split(":")[0]) if n in SPECIAL_NAMES else (
            "  <- weak gap" if n == cf.WEAK_GAP_CELL else "")
        print(f"    C{n:<2} {label:<18} {day['per_cell_incident'][n]:.4f} -> "
              f"{night['per_cell_incident'][n]:.4f}{tag}")
    return ok


# ---------------------------------------------------------------------------
# Reservoir validation (v06 step 2 gate)
# ---------------------------------------------------------------------------

def _euclid(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _record_inbetween(scenario, ticks, seed):
    """Run the field on a scenario and record the six in-between cell
    activations each tick. This is the input the reservoir sees. It does not
    depend on the reservoir (which is read-only), so one recording is reusable
    across every spectral radius."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    field = cf.build_field()
    seq = []
    for values in scenario_inputs(scenario, ticks, rng):
        field.step(values)
        seq.append([field.cells[n].activation for n in cf.IN_BETWEEN_CELLS])
    return seq


def _evolve(field, u_seq, r0):
    """Run the reservoir map over an input sequence from initial state r0.
    Returns the final state. The reservoir matrices live on `field`."""
    r = list(r0)
    for u in u_seq:
        r = field.reservoir_step(r, u)
    return r


def reservoir_probe(args):
    """Step-2 gate: the reservoir state is distinguishable across two different
    input histories, and it obeys the echo state property (it forgets its
    initial state). Plus a spectral radius sweep."""
    apply_overrides(args.set)
    ticks = min(args.ticks, 6000)  # plenty for echo convergence; keep it brisk
    size = cf.RESERVOIR_SIZE
    zeros = [0.0] * size
    probe_rng = random.Random(99)
    r0_alt = [probe_rng.uniform(-1.0, 1.0) for _ in range(size)]

    # Two genuinely different lived histories (different worlds).
    uA = _record_inbetween("day", ticks, args.seed)
    uB = _record_inbetween("bursts", ticks, args.seed)
    # Plus a subtler pair: same world (day), different life (seed). The
    # reservoir should still tell these apart, but by less.
    uC = _record_inbetween("day", ticks, args.seed + 100)

    field = cf.build_field()
    print(f"field {cf.FIELD_VERSION} | RESERVOIR PROBE | seed={args.seed} | "
          f"ticks={ticks} | size={size} | target radius "
          f"{cf.RESERVOIR_SPECTRAL_RADIUS} measured {field.reservoir_radius}")

    # Distinguishability: two histories -> two end states.
    endA = _evolve(field, uA, zeros)
    endB = _evolve(field, uB, zeros)
    endC = _evolve(field, uC, zeros)
    endA2 = _evolve(field, uA, zeros)             # identical-history control
    d_AB = _euclid(endA, endB)                     # different worlds
    d_AC = _euclid(endA, endC)                     # same world, different life
    d_AA = _euclid(endA, endA2)
    state_norm = math.sqrt(sum(x * x for x in endA)) or 1.0

    # Echo state property: same history, two different initial states -> converge.
    echo_Z = _evolve(field, uA, zeros)
    echo_R = _evolve(field, uA, r0_alt)
    echo_init = _euclid(zeros, r0_alt)
    echo_final = _euclid(echo_Z, echo_R)

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("identical histories give identical state",
          d_AA < 1e-9,
          f"distance {d_AA:.2e} (control, expect ~0)")
    check("different worlds are distinguishable",
          d_AB > 0.5 * state_norm,
          f"day vs bursts {d_AB:.4f} ({d_AB / state_norm:.2f}x the state norm)")
    check("same world, different life still distinguishable",
          d_AC > 1e-3,
          f"day vs day {d_AC:.4f} ({d_AC / state_norm:.2f}x the state norm)")
    check("echo state property: forgets initial state",
          echo_final < 0.01,
          f"initial gap {echo_init:.3f} -> final {echo_final:.2e}")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")

    # Spectral radius sweep: ESP should hold below 1.0 and break at/above it.
    print("\n  spectral radius sweep (echo gap = init-state forgetting; "
          "lower is healthier):")
    print("    target  measured  echo_gap_final   distinguish(A,B)   echo state")
    default_radius = cf.RESERVOIR_SPECTRAL_RADIUS
    for target in (0.1, 0.3, 0.6, 0.9, 1.1, 1.5):
        cf.RESERVOIR_SPECTRAL_RADIUS = target
        f = cf.build_field()
        eZ = _evolve(f, uA, zeros)
        eR = _evolve(f, uA, r0_alt)
        gap = _euclid(eZ, eR)
        dab = _euclid(eZ, _evolve(f, uB, zeros))
        esp = "ok" if gap < 0.01 else ("weak" if gap < 0.5 else "BROKEN")
        print(f"    {target:5.1f}   {f.reservoir_radius:7.4f}   {gap:12.2e}   "
              f"{dab:14.4f}   {esp}")
    cf.RESERVOIR_SPECTRAL_RADIUS = default_radius
    return ok


# ---------------------------------------------------------------------------
# Readout validation (v06 step 3 gate)
# ---------------------------------------------------------------------------

def _solve(A, b):
    """Solve A w = b for a small symmetric system by Gaussian elimination with
    partial pivoting. Pure stdlib so results match on any machine."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        pivot = M[col][col] or 1e-12
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col] / pivot
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    return [M[i][n] / (M[i][i] or 1e-12) for i in range(n)]


def _lstsq_r2(X, y, split, ridge=1e-4):
    """Fit a ridge linear model (with bias) on the train split via the normal
    equations, return R^2 on the held-out test split. R^2 is the fraction of
    target variance the features explain; 0 means no better than the mean."""
    nf = len(X[0])
    Xb = [row + [1.0] for row in X]          # bias column
    m = nf + 1
    Xtr, ytr = Xb[:split], y[:split]
    Xte, yte = Xb[split:], y[split:]

    A = [[0.0] * m for _ in range(m)]
    b = [0.0] * m
    for xi, yi in zip(Xtr, ytr):
        for r in range(m):
            b[r] += xi[r] * yi
            xir = xi[r]
            Ar = A[r]
            for c in range(m):
                Ar[c] += xir * xi[c]
    for r in range(m):
        A[r][r] += ridge
    w = _solve(A, b)

    preds = [sum(w[k] * xi[k] for k in range(m)) for xi in Xte]
    mean_y = statistics.mean(yte)
    ss_res = sum((p - yi) ** 2 for p, yi in zip(preds, yte))
    ss_tot = sum((yi - mean_y) ** 2 for yi in yte) or 1e-12
    return 1.0 - ss_res / ss_tot


def _ema(series, alpha=0.02):
    """Exponential moving average: the 'field state' an emitter should express,
    i.e. the ongoing sensory situation rather than the instantaneous value."""
    out = []
    m = 0.0
    for v in series:
        m = (1.0 - alpha) * m + alpha * v
        out.append(m)
    return out


def readout_probe(args):
    """Step-3 gate: the emitter readout from the reservoir tracks its
    loop-partner sense (and the field state behind it) more richly than a direct
    connection from the outer ring.

    Richness is measured as R^2 (variance of the target explained on a held-out
    test split) of an exact least-squares readout, for three feature sets:
    the reservoir state, the emitter's direct ring neighbours (the literal
    "direct connection from the outer ring"), and all six in-between cells.
    The gate is reservoir vs the direct ring connection, on the field-state
    target. The raw same-tick sense is reported alongside for context."""
    apply_overrides(args.set)
    random.seed(args.seed)
    rng = random.Random(args.seed + 1)
    field = cf.build_field()
    ticks = args.ticks

    neighbours = {  # ring neighbours of each emitter
        "emitter_speaker": [(0 - 1) % cf.CELL_COUNT, (0 + 1) % cf.CELL_COUNT],  # 11, 1
        "emitter_led":     [(6 - 1) % cf.CELL_COUNT, (6 + 1) % cf.CELL_COUNT],  # 5, 7
    }

    res, inbetween = [], []
    neigh = {hw: [] for hw in neighbours}
    senses = {"sound": [], "light": []}
    for values in scenario_inputs("day", ticks, rng):
        field.step(values)
        res.append(list(field.reservoir_state))
        inbetween.append([field.cells[n].activation for n in cf.IN_BETWEEN_CELLS])
        for hw, ns in neighbours.items():
            neigh[hw].append([field.cells[n].activation for n in ns])
        senses["sound"].append(values["sound"])
        senses["light"].append(values["light"])

    field_state = {s: _ema(senses[s]) for s in senses}

    print(f"field {cf.FIELD_VERSION} | READOUT PROBE | seed={args.seed} | "
          f"ticks={ticks} | reservoir radius {field.reservoir_radius}")
    print("  richness = R^2 on held-out test split (variance explained; "
          "0 = no better than the mean)")

    split = int(ticks * 0.7)
    checks = []

    for hw, target_name in cf.READOUT_TARGET.items():
        fs = field_state[target_name]
        raw = senses[target_name]

        res_fs = _lstsq_r2(res, fs, split)
        loc_fs = _lstsq_r2(neigh[hw], fs, split)
        all_fs = _lstsq_r2(inbetween, fs, split)
        res_raw = _lstsq_r2(res, raw, split)
        loc_raw = _lstsq_r2(neigh[hw], raw, split)

        print(f"\n  {hw} tracking {target_name}:")
        print(f"    field state (recent average)   reservoir R2 {res_fs:+.3f} | "
              f"direct ring R2 {loc_fs:+.3f} | all in-between R2 {all_fs:+.3f}")
        print(f"    raw same-tick {target_name:<7}          reservoir R2 {res_raw:+.3f} | "
              f"direct ring R2 {loc_raw:+.3f}")

        better = res_fs > loc_fs + 0.02   # clearly beats the direct connection
        checks.append(better)
        print(f"    -> reservoir {'richer than' if better else 'NOT richer than'} "
              f"the direct ring connection (+{res_fs - loc_fs:.3f} R2)")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} emitters)")
    print("  note: the reservoir roughly ties the raw in-between cells here; its "
          "echo adds\n  little on a pure tracking task. Temporal memory pays off "
          "on memory tasks\n  (recalling past events) and once the loop closes "
          "(step 7).")
    return ok


# ---------------------------------------------------------------------------
# Predictive cell validation (v06 step 4 gate)
# ---------------------------------------------------------------------------

def predictive_probe(args):
    """Step-4 gate: with the predictive cell, the field no longer flattens to
    uniform under steady input.

    'Uniform' is measured as the spread (std) of the cells' average activation
    across the ring. A leaky field under steady input is driven by its
    homeostatic gain to one shared activation target, so the spread collapses
    toward zero: a flat wash. The predictive cell has no shared target and emits
    surprise, so the spread holds. We compare both models on a steady scenario,
    and report each model's differentiation on the realistic day scenario too."""
    apply_overrides(args.set)
    ticks = args.ticks
    default_model = cf.CELL_MODEL

    def run(model, scenario):
        cf.CELL_MODEL = model
        random.seed(args.seed)
        rng = random.Random(args.seed + 1)
        field = cf.build_field()
        for values in scenario_inputs(scenario, ticks, rng):
            field.step(values)
        return field

    out = {}
    for model in ("leaky", "predictive"):
        steady_field = run(model, "steady")
        avg = [c.avg_activation for c in steady_field.cells.values()]
        out[model] = {
            "steady_uniformity": statistics.pstdev(avg),
            "steady_mean_act": statistics.mean(avg),
            "day_diff": ring_metrics(run(model, "day"))["differentiation"],
        }
    cf.CELL_MODEL = default_model

    lk, pr = out["leaky"], out["predictive"]
    print(f"field {cf.FIELD_VERSION} | PREDICTIVE GATE | seed={args.seed} | ticks={ticks}")
    print("\n  steady input (activation spread across cells; higher = more "
          "differentiated, less flat):")
    print(f"    leaky        avg-activation std {lk['steady_uniformity']:.5f}  "
          f"(mean {lk['steady_mean_act']:.4f})")
    print(f"    predictive   avg-activation std {pr['steady_uniformity']:.5f}  "
          f"(mean {pr['steady_mean_act']:.4f})")
    ratio = pr["steady_uniformity"] / (lk["steady_uniformity"] or 1e-12)
    print(f"    -> predictive holds {ratio:.1f}x the spread the leaky field flattens away")

    print("\n  day scenario (overall structural differentiation):")
    print(f"    leaky        differentiation {lk['day_diff']:.4f}")
    print(f"    predictive   differentiation {pr['day_diff']:.4f}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("predictive stays differentiated under steady input",
          ratio >= 5.0,
          f"{ratio:.1f}x the leaky spread (leaky flattens to {lk['steady_uniformity']:.5f})")
    check("predictive is more differentiated on the day scenario",
          pr["day_diff"] > 1.3 * lk["day_diff"],
          f"predictive {pr['day_diff']:.4f} vs leaky {lk['day_diff']:.4f}")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  note: the predictive cell correctly goes quiet on slow, predictable\n"
          "  senses (weather, daylight) - it feeds on surprise, so it does not\n"
          "  build structure where there is nothing to be surprised by. Keeping\n"
          "  the steady side alive at night is the closed loop's job (step 7).")
    return ok


# ---------------------------------------------------------------------------
# Expression memory: the passive recorder (Evolution 2, step 1)
# ---------------------------------------------------------------------------
#
# See "Creature Expression as Memory" (direction doc). This is step 1 only:
# RECORD. The creature's lasting memory is the graph of its own expressions.
# Each tick the decoder turns the field into a body signal (arousal, balance,
# tempo, and whether it speaks). We quantize that signal into a node, draw a
# weighted edge from the previous node, and decay unused paths. Nothing feeds
# back into the field: this records, it does not yet bias. Bias is step 2,
# novelty step 3.
#
# The expression vector is taken from what the body actually emits, as the
# decoder produces it, not a hand-picked feature list:
#
#     E = (arousal, balance01, tempo, voiced)
#
# arousal/tempo are already 0..1; balance is -1..1 remapped to 0..1; voiced is
# 1.0 when the decoder would send a VOX tone this tick, else 0.0.


def _record_expression(scenario, ticks, seed, bins, decay):
    """Run one life and return its expression graph. Passive: the decoder reads
    the field, the graph records the decoder, nothing feeds back."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()
    graph = ExpressionGraph(bins=bins, decay=decay)
    for values in scenario_inputs(scenario, ticks, rng):
        state = field.step(values)
        signal = decoder.read(state)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        graph.observe(expression_vector(signal, speaker))
    return graph


def expression_memory_probe(args):
    """Step-1 gate for expression-as-memory: an autobiography forms, and two
    different lives produce distinguishable graphs (more different from each
    other than two lives of the same kind). RECORD only, no feedback."""
    apply_overrides(args.set)
    ticks = args.ticks
    bins = args.expr_bins
    decay = args.expr_decay

    # Three lives. A and C are the same kind of world (different seed); B is a
    # different world. Identity holds if A sits closer to C than to B.
    gA = _record_expression("day", ticks, args.seed, bins, decay)
    gB = _record_expression("bursts", ticks, args.seed, bins, decay)
    gC = _record_expression("day", ticks, args.seed + 100, bins, decay)

    d_AB = graph_distance(gA, gB)   # different lives
    d_AC = graph_distance(gA, gC)   # same kind of life

    print(f"field {cf.FIELD_VERSION} | EXPRESSION MEMORY (RECORD) | seed={args.seed} "
          f"| ticks={ticks} | bins={bins} decay={decay}")
    print("\n  three autobiographies (nodes = expression states, "
          "edges = transitions):")
    for name, g in (("A day    ", gA), ("B bursts ", gB), ("C day+100", gC)):
        print(f"    {name}  nodes {len(g.visits):3d}  edges {len(g.count):4d}  "
              f"live-edges {len(g.edge_weight):4d}  visit-entropy {g.visit_entropy():.3f}")

    print("\n  identity distance (0 = identical, 1 = nothing shared):")
    print(f"    A vs B  (different lives)    {d_AB:.3f}")
    print(f"    A vs C  (same kind of life)  {d_AC:.3f}")

    print("\n  A's recurring moves (top transitions between states, dwell "
          "dropped; src -> dst as [A,B,T,voiced] bins):")
    for (src, dst), c in gA.top_motifs(5, include_self=False):
        print(f"    {src} -> {dst}   x{c}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("an autobiography forms",
          len(gA.visits) >= 3 and len(gA.count) >= 3,
          f"A has {len(gA.visits)} nodes, {len(gA.count)} edges")
    check("two different lives are distinguishable",
          d_AB > 0.05,
          f"A vs B distance {d_AB:.3f}")
    check("identity tracks the life, not the seed",
          d_AB > 1.3 * d_AC,
          f"different-life {d_AB:.3f} > 1.3x same-life {d_AC:.3f} "
          f"(ratio {d_AB / (d_AC or 1e-9):.2f})")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  note: this is RECORD only. The graph does not yet bias expression\n"
          "  (step 2) or push for novelty (step 3). It proves the autobiography\n"
          "  forms and is legible: two lives, two different graphs.")
    return ok


# ---------------------------------------------------------------------------
# Expression memory: bias (Evolution 2, step 2)  [sim experiment, ahead of queue]
# ---------------------------------------------------------------------------
#
# Step 1 recorded. Step 2 lets the record bias the next expression: after a
# state, nudge the creature toward where it usually goes from there. One mixing
# weight w, field against habit. This is where it becomes memory.
#
# It also opens the failure the direction doc names: too much habit and the
# creature falls into a groove, dwelling in one place, as dead as a flat line.
# So this sweeps w and watches for it. Per the doc this is a sim probe ahead of
# the dark-room proof, not a hardware commitment. Novelty (step 3) is the
# counter-force, and is not built here.


def _run_biased(scenario, ticks, seed, bins, decay, w):
    """One life where the graph biases expression by weight w. Returns the graph
    built from what it actually expressed (the biased output)."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()
    graph = ExpressionGraph(bins=bins, decay=decay)
    for values in scenario_inputs(scenario, ticks, rng):
        state = field.step(values)
        signal = decoder.read(state)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        raw = expression_vector(signal, speaker)
        prev = graph.prev
        biased = raw
        if w > 0.0 and prev is not None:
            pred = graph.predict_next(prev)
            if pred is not None:
                biased = tuple((1.0 - w) * r + w * p for r, p in zip(raw, pred))
        graph.observe(biased)
    return graph


def _habit_stats(graph):
    """Concentration of behaviour. dwell = fraction of transitions that stay
    put; coverage = share of the moves (non-dwell) taken by the top 5 motifs;
    entropy = node-visit spread (low = collapsed into a groove)."""
    total = sum(graph.count.values()) or 1
    dwell = sum(c for (s, d), c in graph.count.items() if s == d) / total
    moves = [(e, c) for e, c in graph.count.items() if e[0] != e[1]]
    move_total = sum(c for _, c in moves) or 1
    top = sorted((c for _, c in moves), reverse=True)[:5]
    coverage = sum(top) / move_total
    return {
        "nodes": len(graph.visits),
        "dwell": dwell,
        "coverage": coverage,
        "entropy": graph.visit_entropy(),
    }


def expression_bias_probe(args):
    """Step-2 probe: sweep the field-vs-habit mixing weight. Bias should
    concentrate behaviour into motifs; too much should collapse it into a
    groove. RECORD plus BIAS, still no feedback into the field."""
    apply_overrides(args.set)
    ticks = args.ticks
    bins = args.expr_bins
    decay = args.expr_decay
    weights = [0.0, 0.4, 0.7, 0.95]

    rows = {w: _habit_stats(_run_biased("day", ticks, args.seed, bins, decay, w))
            for w in weights}

    print(f"field {cf.FIELD_VERSION} | EXPRESSION MEMORY (BIAS) | seed={args.seed} "
          f"| ticks={ticks} | bins={bins} decay={decay}")
    print("\n  field-vs-habit sweep (w=0 is pure field/step-1; w->1 is pure habit):")
    print("    w       nodes   dwell   top5-move-coverage   visit-entropy")
    for w in weights:
        r = rows[w]
        print(f"    {w:4.2f}   {r['nodes']:5d}   {r['dwell']:5.3f}   "
              f"{r['coverage']:18.3f}   {r['entropy']:.3f}")

    base, mid, high = rows[0.0], rows[0.7], rows[0.95]

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("bias forms habit (concentrates the moves)",
          mid["coverage"] > base["coverage"],
          f"top5 move-coverage {base['coverage']:.3f} (w=0) -> "
          f"{mid['coverage']:.3f} (w=0.7)")
    check("over-bias collapses toward a groove",
          high["dwell"] > base["dwell"] and high["entropy"] < base["entropy"],
          f"dwell {base['dwell']:.3f} (w=0) -> {high['dwell']:.3f} (w=0.95); "
          f"entropy {base['entropy']:.3f} -> {high['entropy']:.3f}")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  reading: a little memory sharpens motifs; too much memory eats the\n"
          "  creature, which is why step 3 (novelty as a force) has to follow.")
    return ok


# ---------------------------------------------------------------------------
# Expression memory: novelty (Evolution 2, step 3)  [sim experiment]
# ---------------------------------------------------------------------------
#
# Step 2 showed bias has no stable middle: more habit slides to a groove. Step 3
# adds the counter-force the doc demands. Novelty is a drive, not a readout: at
# each tick it looks at where bias wants to go and pushes toward the least-
# explored adjacent state instead. Directed, not noise, which is the bar the
# direction doc set (a probe must aim somewhere, a twitch does not).
#
# The blend is two stages. Bias first (field vs habit, weight w), then novelty
# pulls that result toward the least-explored neighbour. Novelty is adaptive: it
# fires in proportion to how worn the creature is right now (a dwell counter),
# so it pushes hardest in a groove and eases off on fresh ground. That self-
# limiting is what a constant push lacks, and what lets a stable middle exist:
#
#     base  = (1 - w) * field + w * usual_next
#     eff_n = n * (how worn the creature is right now)
#     final = (1 - eff_n) * base + eff_n * least_explored_neighbour
#
# The probe maps w against n and looks for the band where motifs survive (more
# concentrated than pure field) without collapse. That band is temperament.


def _run_bias_novelty(scenario, ticks, seed, bins, decay, w, n, dwell_ref=6):
    """One life under habit weight w and adaptive novelty weight n. Novelty
    fires in proportion to how long the creature has been stuck in one node, so
    it only pushes when worn in. Returns the graph of what it expressed. Still
    no feedback into the field."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()
    graph = ExpressionGraph(bins=bins, decay=decay)
    dwell_run = 0
    last_node = None
    for values in scenario_inputs(scenario, ticks, rng):
        state = field.step(values)
        signal = decoder.read(state)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        raw = expression_vector(signal, speaker)
        prev = graph.prev
        base = raw
        if w > 0.0 and prev is not None:
            pred = graph.predict_next(prev)
            if pred is not None:
                base = tuple((1.0 - w) * r + w * p for r, p in zip(raw, pred))
        final = base
        if n > 0.0:
            worn = min(1.0, dwell_run / dwell_ref)   # 0 fresh, 1 stuck
            eff_n = n * worn
            if eff_n > 0.0:
                nov = _novelty_target(graph, base, bins)
                final = tuple((1.0 - eff_n) * b + eff_n * v
                              for b, v in zip(base, nov))
        final = tuple(min(1.0, max(0.0, x)) for x in final)
        node = quantize_expression(final, bins)
        dwell_run = dwell_run + 1 if node == last_node else 0
        last_node = node
        graph.observe(final)
    return graph


def expression_novelty_probe(args):
    """Step-3 probe: map habit weight w against novelty weight n. Without
    novelty, rising habit collapses the creature into a groove (step 2). An
    adaptive novelty force, firing only where the creature is worn in, should
    open a band of moderate w and n where motifs survive without collapse. That
    band is temperament."""
    apply_overrides(args.set)
    ticks = args.ticks
    bins = args.expr_bins
    decay = args.expr_decay
    ws = [0.0, 0.6, 0.8, 0.95]
    ns = [0.0, 0.2, 0.4]

    grid = {(w, n): _habit_stats(
        _run_bias_novelty("day", ticks, args.seed, bins, decay, w, n))
        for w in ws for n in ns}
    field_ref = grid[(0.0, 0.0)]

    print(f"field {cf.FIELD_VERSION} | EXPRESSION MEMORY (NOVELTY) | seed={args.seed} "
          f"| ticks={ticks} | bins={bins}")
    print(f"\n  reference, pure field (w=0 n=0): nodes {field_ref['nodes']}, "
          f"coverage {field_ref['coverage']:.3f}, entropy {field_ref['entropy']:.3f}")

    def grid_table(metric, label):
        print(f"\n  {label}:")
        print("    w \\\\ n  " + "".join(f"   n={n:.1f}" for n in ns))
        for w in ws:
            cells = "".join(f"   {grid[(w, n)][metric]:5.3f}" for n in ns)
            print(f"    w={w:.2f}{cells}")

    grid_table("entropy", "visit-entropy (low = groove collapse; ~field is healthy)")
    grid_table("coverage", "top5-move-coverage (high = strong motifs / habit)")

    # Temperament band: novelty on, habit sharper than pure field, not collapsed.
    fc = field_ref["coverage"]
    band = sorted((w, n) for (w, n), r in grid.items()
                  if w > 0 and n > 0 and r["coverage"] > fc
                  and r["entropy"] >= 0.3 and r["nodes"] >= 15)

    if band:
        print("\n  temperament band (habit above field, no collapse):")
        for (w, n) in band:
            r = grid[(w, n)]
            print(f"    w={w:.2f} n={n:.1f}   nodes {r['nodes']:3d}   "
                  f"coverage {r['coverage']:.3f}   entropy {r['entropy']:.3f}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    collapse_cell = grid[(ws[-1], 0.0)]
    check("bias alone collapses (no novelty, high habit)",
          collapse_cell["entropy"] < 0.05,
          f"w={ws[-1]} n=0 entropy {collapse_cell['entropy']:.3f}, "
          f"nodes {collapse_cell['nodes']}")
    check("adaptive novelty opens a temperament band",
          len(band) >= 1,
          f"{len(band)} (w,n) cell(s) keep motifs above field "
          f"(coverage>{fc:.3f}) without collapse")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  reading: habit and novelty are a pair. Habit alone collapses to a\n"
          "  groove; adaptive novelty, firing only where the creature is worn\n"
          "  in, holds a middle open. The band where motifs survive without\n"
          "  collapse is the creature's temperament.")
    return ok


# ---------------------------------------------------------------------------
# The loop and the dark-room probe (Evolution 1, sim rehearsal)
# ---------------------------------------------------------------------------
#
# The expression-memory steps never fed the body's output back to the field.
# This one does, which is the whole point. Close the loop in sim: the creature's
# own light and voice return as light and sound input next tick. Add a curiosity
# drive that wakes when the creature goes flat and pokes the loop with a varying
# probe. A steady self-loop is as predictable as a steady room, so a predictive
# cell would go quiet on it; curiosity has to vary to keep surprise alive. A
# small forward model learns the loop gain, so the poke is directed, not a
# twitch.
#
# The dark-room test (the result the Risks doc keeps asking for): in a dark,
# silent room a creature with no loop goes flat, while a creature with the loop
# and curiosity stays active on its own, bounded, and learns its own echo.


def _run_loop(seed, ticks, gain, curiosity, bored_floor=0.3):
    """Run the field in a dark, silent room with the body's output looped back
    into its senses. gain=0, curiosity=0 is the open-loop control. Returns the
    arousal timeline, forward-model error series, and summary stats."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()

    last_a = 0.0
    last_voiced = 0.0
    g_model = 0.0            # learned forward gain: predict light feedback from A
    arousal = []
    err = []

    for _ in range(ticks):
        # curiosity gates on the creature's current arousal: poke hard when flat,
        # not at all when already active. With a sub-unit loop gain that makes a
        # relaxation oscillator, bounded by construction (no runaway) yet never
        # fully still. Random bursts keep the poke unpredictable, so the
        # predictive field cannot habituate to a smooth self-signal and flatten.
        bored = max(0.0, bored_floor - last_a)
        burst = rng.random() if rng.random() < 0.15 else 0.1
        probe = curiosity * bored * burst

        light_in = min(1.0, gain * last_a + probe)
        sound_in = min(1.0, gain * last_voiced + 0.5 * probe)

        state = field.step({
            "light": light_in,
            "sound": sound_in,
            "motion": 0.0,
            "weather": 0.0,
        })
        signal = decoder.read(state)
        a = float(signal.get("A", 0.0) or 0.0)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        voiced = 1.0 if voice_command_from_signal(signal, speaker) else 0.0

        # forward model: predict the loop's light feedback from last arousal,
        # learn it by a delta rule. The error falls as it learns its own echo.
        pred_fb = g_model * last_a
        actual_fb = gain * last_a
        err.append(abs(actual_fb - pred_fb))
        g_model += 0.05 * (actual_fb - pred_fb) * last_a

        arousal.append(a)
        last_a, last_voiced = a, voiced

    half = ticks // 2
    tail = arousal[half:]
    span = max(1, ticks // 10)
    return {
        "mean_a": statistics.mean(tail),
        "std_a": statistics.pstdev(tail),
        "max_a": max(tail),            # steady-state max, ignores warm-up spike
        "peak_a": max(arousal),        # whole-run peak, for info
        "err_early": statistics.mean(err[:span]),
        "err_late": statistics.mean(err[-span:]),
    }


def darkroom_probe(args):
    """The dark-room test: open-loop control vs closed-loop-with-curiosity, in a
    dark silent room. The control should go flat; the loop creature should stay
    active on its own, varying and bounded, and learn its own echo."""
    apply_overrides(args.set)
    ticks = args.ticks
    gain = args.loop_gain
    cur = args.curiosity

    control = _run_loop(args.seed, ticks, gain=0.0, curiosity=0.0)
    variant = _run_loop(args.seed, ticks, gain=gain, curiosity=cur)

    print(f"field {cf.FIELD_VERSION} | DARK-ROOM PROBE | seed={args.seed} "
          f"| ticks={ticks} | loop_gain={gain} curiosity={cur}")
    print("\n  dark, silent room (no external input):")
    print("                  mean-arousal   std(tail)   max   fwd-err early->late")
    print(f"    control (no loop)   {control['mean_a']:.3f}        "
          f"{control['std_a']:.3f}     {control['max_a']:.3f}   "
          f"{control['err_early']:.3f} -> {control['err_late']:.3f}")
    print(f"    loop + curiosity    {variant['mean_a']:.3f}        "
          f"{variant['std_a']:.3f}     {variant['max_a']:.3f}   "
          f"{variant['err_early']:.3f} -> {variant['err_late']:.3f}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("control goes flat (no loop, dark room)",
          control["mean_a"] < 0.05,
          f"control mean arousal {control['mean_a']:.3f}")
    check("the loop creature stays alive on its own",
          variant["mean_a"] > control["mean_a"] + 0.10 and variant["mean_a"] > 0.10,
          f"loop mean arousal {variant['mean_a']:.3f} vs control "
          f"{control['mean_a']:.3f}")
    check("its activity stays restless (not flat, not stuck on)",
          variant["std_a"] > 0.03,
          f"tail std {variant['std_a']:.3f} vs control {control['std_a']:.3f}")
    check("bounded, not screaming into its own eye",
          variant["max_a"] < 0.99,
          f"steady-state max arousal {variant['max_a']:.3f}")
    check("the probing is directed (learns its own echo)",
          variant["err_late"] < 0.6 * variant["err_early"],
          f"fwd error {variant['err_early']:.3f} -> {variant['err_late']:.3f}")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  reading: in an empty room the open-loop creature goes quiet. The\n"
          "  looped, curious one makes its own gradient, stays restless and\n"
          "  bounded, and learns its own echo, with nothing from the room. That\n"
          "  self-generated, learned activity is the result the project has been\n"
          "  after. Sim rehearsal; the hardware run on placed sensors is the\n"
          "  real proof.")
    return ok


# ---------------------------------------------------------------------------
# The forward model: predicting its own echo (the predict cycle)
# ---------------------------------------------------------------------------
#
# The dark-room probe above learned one scalar against a loop gain it was handed.
# This is the real one, the module the collector runs: it only ever sees what
# the body emitted (the strip frame, any tone) and what the raw sensors returned,
# and has to work out for itself which part of the room is its own doing.
#
# The room is simulated: a sun that rises and sets, sensor noise, a lamp that
# can be switched, a hand that can cover the strip, a mic with its own bursts.
# The creature's life is the same in every run (same seed, same field); only the
# room differs, so any difference in what the model learns is the room's.

FORWARD_COUPLING = (20.0, 35.0, 10.0, 60.0)   # lux at the sensor per RGBW channel at full
FORWARD_ECHO = 120000.0                       # mic rms above the room per unit tone volume
FORWARD_VOICE_GAP = 20                        # ticks between tones, as in the collector
FORWARD_PITCH_ECHO = 15000.0                  # mic level at the tone's own pitch, per unit volume
# How loud the speaker is across its range, shaped like the real one measured on
# 2 October 2026: weak at the bottom, peaks near 330 and 400 Hz, a dip between.
FORWARD_VOICE_RESPONSE = [(220.0, 0.15), (250.0, 0.20), (290.0, 0.25), (325.0, 1.00),
                          (350.0, 0.90), (370.0, 0.30), (400.0, 1.20), (440.0, 1.00),
                          # above 440 Hz from the sweep of 7 October 2026
                          (476.0, 1.6), (600.0, 1.75), (756.0, 3.4), (952.0, 5.8),
                          (1200.0, 4.2), (1512.0, 4.0), (1905.0, 3.0)]


def _voice_response(freq):
    """The simulated speaker's loudness at a pitch, relative to its usual echo."""
    points = FORWARD_VOICE_RESPONSE
    if freq <= points[0][0]:
        return points[0][1]
    for (f0, r0), (f1, r1) in zip(points, points[1:]):
        if freq <= f1:
            return r0 + (r1 - r0) * (freq - f0) / (f1 - f0)
    return points[-1][1]


def _heard(voice, room, burst=0.0, muffle=1.0):
    """What the v06.8 body would report for a tone in the simulated room."""
    # Room noise is spread over every pitch, so only a sliver of a burst lands
    # on the one the creature is listening for.
    room_heard = 150.0 + 100.0 * room.random() + 0.004 * burst * room.random()
    own = (FORWARD_PITCH_ECHO * _voice_response(voice["freq"]) * voice["vol"]
           * voice.get("level", 1.0) * (0.85 + 0.3 * room.random()) * muffle)
    return {"freq": voice["freq"], "heard": own + room_heard * (0.5 + room.random()),
            "room_heard": room_heard}


def _run_forward(seed, ticks, coupling, lamp_every=0, cover=None, noisy=False, ear=False):
    """One life with the forward model watching. `coupling` is how much of each
    strip channel reaches the light sensor (all zero = the sensor cannot see the
    strip). `noisy` fills the room with bursts louder than the creature's own
    tones. `ear` gives the body the v06.8 firmware, which reports what it heard
    at the tone's own pitch. Returns the model and what it tracked along the way."""
    random.seed(seed)
    rng = random.Random(seed + 1)
    room = random.Random(seed + 2)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()
    model = ForwardModel()

    day_len = 7200
    lamp_on = False
    last_voice = -FORWARD_VOICE_GAP
    switch_err = []
    plain_err = []
    full = []
    voice_miss = []   # per tone: how far off the prediction was, as a share of the echo

    for t, values in enumerate(scenario_inputs("day", ticks, rng)):
        state = field.step(values)
        signal = decoder.read(state)
        u = frame_rgbw(signal["pixels"])
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        voice = voice_params_from_signal(signal, speaker)
        if voice and t - last_voice < FORWARD_VOICE_GAP:
            voice = None
        elif voice:
            last_voice = t

        sun = max(0.0, math.sin((t % day_len) / day_len * 2 * math.pi))
        ambient = 20.0 + 600.0 * sun
        switched = bool(lamp_every and t > 0 and t % lamp_every == 0)
        if switched:
            lamp_on = not lamp_on
        if lamp_on:
            ambient += 300.0
        covered = bool(cover and cover[0] <= t < cover[1])
        own = 0.0 if covered else sum(coupling[i] * u[i] for i in range(4))
        lux = ambient + own + room.gauss(0.0, 0.5)

        rms_mean = 4000.0 + 800.0 * room.random()
        if noisy:
            burst = 300000.0 * room.random() if room.random() < 0.3 else 0.0
        else:
            burst = 30000.0 * room.random() if room.random() < 0.03 else 0.0
        rms_max = rms_mean + 1500.0 * room.random() + burst
        vox = None
        if voice:
            wobble = 0.85 + 0.3 * room.random()
            rms_max += FORWARD_ECHO * voice["vol"] * wobble
            if ear:
                vox = _heard(voice, room, burst)

        result = model.step(
            {"rgbw": u, "voice": voice},
            {"lux": lux, "rms_mean": rms_mean, "rms_max": rms_max, "vox": vox},
        )
        if result["light"]:
            (switch_err if switched else plain_err).append(abs(result["light"]["err"]))
        full.append(model.light.lux_at_full())
        sound = result["sound"]
        if sound and sound.get("voiced"):
            size = max(abs(sound["excess"]), abs(sound["pred"]), 1e-6)
            voice_miss.append(min(1.0, abs(sound["err"]) / size))

    return {
        "model": model,
        "voice_miss": voice_miss,
        "full": full,
        "switch_err": statistics.mean(switch_err) if switch_err else 0.0,
        "plain_err": statistics.mean(plain_err) if plain_err else 0.0,
    }


def forward_probe(args):
    """The forward-model gate: it learns its own light and voice from what it
    emits and what comes back, takes no credit for a room it cannot see itself
    in, keeps the world's changes as the world's, and notices when its own
    light stops reaching its eye."""
    apply_overrides(args.set)
    ticks = args.ticks
    truth = sum(FORWARD_COUPLING)
    cover = (int(ticks * 0.75), int(ticks * 0.75) + 600)

    seen = _run_forward(args.seed, ticks, FORWARD_COUPLING)
    blind = _run_forward(args.seed, ticks, (0.0, 0.0, 0.0, 0.0))
    lamp = _run_forward(args.seed, ticks, FORWARD_COUPLING, lamp_every=1500)
    hand = _run_forward(args.seed, ticks, FORWARD_COUPLING, cover=cover)
    loud = _run_forward(args.seed, ticks, FORWARD_COUPLING, noisy=True)
    loud_ear = _run_forward(args.seed, ticks, FORWARD_COUPLING, noisy=True, ear=True)

    print(f"field {cf.FIELD_VERSION} | FORWARD MODEL | seed={args.seed} | ticks={ticks}")
    print(f"\n  the room: strip adds {truth:.0f} lux at full, a tone adds "
          f"{FORWARD_ECHO:.0f} rms per unit volume")
    print("\n  light                 learned-lux-at-full   explained   weights (r g b w)")
    for name, run_ in (("sensor sees strip ", seen), ("sensor is blind   ", blind),
                       ("lamp switched     ", lamp)):
        light = run_["model"].light
        print(f"    {name}      {light.lux_at_full():7.1f}          "
              f"{light.explained():6.3f}     "
              + " ".join(f"{w:6.1f}" for w in light.w))
    sound = seen["model"].sound
    print(f"\n  sound: learned echo {sound.gain:.0f}, explained {sound.explained():.3f}, "
          f"{sound.voiced} tones")
    by_peak, by_pitch = loud["model"].sound, loud_ear["model"].sound
    print(f"  noisy room, listening for loudness: explained {by_peak.explained():.3f}")
    late = loud_ear["voice_miss"][len(loud_ear["voice_miss"]) * 2 // 3:]
    late_miss = statistics.mean(late) if late else 1.0
    bands = sum(1 for n in by_pitch.pitch_n if n)
    print(f"  noisy room, listening at its own pitch: explained {by_pitch.explained():.3f}, "
          f"{bands} bands of its voice tried, late tones off by {late_miss * 100:.0f}%")
    print(f"  lamp: error on a switch tick {lamp['switch_err']:.1f} lux, "
          f"on an ordinary tick {lamp['plain_err']:.2f} lux")
    at_start = hand["full"][cover[0] - 1]
    at_end = hand["full"][cover[1] - 1]
    print(f"  hand over the strip for {cover[1] - cover[0]} ticks: learned lux "
          f"{at_start:.1f} -> {at_end:.1f}, then back to {hand['full'][-1]:.1f}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    light = seen["model"].light
    check("it learns its own light",
          abs(light.lux_at_full() - truth) < 0.2 * truth,
          f"learned {light.lux_at_full():.1f} lux at full, room has {truth:.0f}")
    check("the prediction beats assuming nothing",
          light.explained() > 0.4,
          f"explains {light.explained():.3f} of the change on ticks it acted")
    blind_light = blind["model"].light
    check("it takes no credit for a room it cannot see itself in",
          abs(blind_light.lux_at_full()) < 0.1 * truth and blind_light.explained() < 0.1,
          f"blind sensor: learned {blind_light.lux_at_full():.1f} lux, "
          f"explained {blind_light.explained():.3f}")
    lamp_light = lamp["model"].light
    check("a lamp is the world, not itself",
          abs(lamp_light.lux_at_full() - truth) < 0.2 * truth
          and lamp["switch_err"] > 20.0 * lamp["plain_err"],
          f"learned {lamp_light.lux_at_full():.1f} lux with the lamp switching; "
          f"switch error {lamp['switch_err']:.0f} vs {lamp['plain_err']:.2f}")
    check("it notices its light no longer reaching its eye",
          at_end < 0.6 * at_start and abs(hand["full"][-1] - truth) < 0.3 * truth,
          f"learned lux falls {at_start:.1f} -> {at_end:.1f} while covered, "
          f"recovers to {hand['full'][-1]:.1f}")
    check("it learns its own voice",
          abs(sound.gain - FORWARD_ECHO) < 0.2 * FORWARD_ECHO and sound.explained() > 0.5,
          f"learned echo {sound.gain:.0f} vs {FORWARD_ECHO:.0f}, "
          f"explained {sound.explained():.3f}")

    check("listening at its own pitch tells its voice from a noisy room",
          by_pitch.explained() > 0.5 and by_pitch.explained() > by_peak.explained() + 0.2
          and late_miss < 0.3,
          f"explained {by_pitch.explained():.3f} at its pitch vs {by_peak.explained():.3f} "
          f"by loudness; late tones predicted to within {late_miss * 100:.0f}%")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    print("  reading: from nothing but what it emits and what comes back, the\n"
          "  model separates its own light and voice from the room. Sim rehearsal:\n"
          "  the real coupling is whatever tools/loop_probe.py measures on the\n"
          "  body. Passive. Nothing here feeds the field yet.")
    return ok


# ---------------------------------------------------------------------------
# The loop reaches the field, and curiosity (v06.9)
# ---------------------------------------------------------------------------
#
# Until here the forward model only watched. Now what it reports is felt: the
# two loop cells get how strongly the creature just sensed its own voice or
# light, weighted by how wrong it was. And when nothing has surprised it for a
# while it probes, with a lift in the strip's white and, rarely, a short tone.
#
# _run_body steps the field, the decoder, the forward model and the curiosity
# drive in the same order the collector does, against a simulated room. Control
# and variant differ only in whether the loop is felt and whether it may probe.

def _run_body(seed, ticks, inputs, coupling, feel=True, probes=True, muffle=None, cover=None,
              daylight=False):
    """One life on a simulated body. `inputs` yields the room's senses, `coupling`
    is how much of each strip channel reaches the light sensor. `muffle` and
    `cover` are (start, end) tick ranges where the speaker is covered or the
    strip is hidden from the sensor. `daylight` gives the room the same smooth
    sun as the forward gate; without it the room is dark. Returns per-tick
    tracks and the models."""
    saved_gain = cf.LOOP_FEEL_GAIN
    if not feel:
        cf.LOOP_FEEL_GAIN = 0.0
    random.seed(seed)
    room = random.Random(seed + 2)
    field = cf.build_field()
    decoder = ExpressionDecoderV06()
    model = ForwardModel()
    curiosity = Curiosity(seed + 3) if probes else None

    last_voice = -FORWARD_VOICE_GAP
    action = None
    returned = None
    out = {"arousal": [], "c1": [], "c7": [], "events": [], "tones": [], "feel_light": [],
           "probe_light": [], "hidden": [], "full_light": []}

    for t, values in enumerate(inputs):
        result = model.step(action, returned) if action is not None else None
        state = field.step(values, loop=(result or {}).get("feel"))

        probe = None
        asleep = state["metabolism"]["mode"] == "sleep"
        if curiosity is not None:
            probe = curiosity.step(state["metabolism"]["ticks_since_event"], model.sound)
            if asleep:
                probe = None

        signal = decoder.read(state)
        pixels = signal["pixels"]
        if probe and probe["light"]:
            pixels = lift_white(pixels, probe["light"], 200)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        voice = voice_params_from_signal(signal, speaker)
        probe_tone = False
        previous_voice = last_voice
        if voice and t - last_voice < FORWARD_VOICE_GAP:
            voice = None
        elif not voice and probe and probe["voice"] and t - last_voice >= FORWARD_VOICE_GAP:
            voice = dict(probe["voice"])
            probe_tone = True
        if voice:
            last_voice = t
            if curiosity is not None:
                curiosity.note_voice()

        u = frame_rgbw(pixels)
        covered = bool(cover and cover[0] <= t < cover[1])
        muffled = bool(muffle and muffle[0] <= t < muffle[1])
        own_light = 0.0 if covered else sum(coupling[i] * u[i] for i in range(4))
        sun = max(0.0, math.sin((t % 7200) / 7200 * 2 * math.pi)) if daylight else 0.0
        lux = 20.0 * daylight + 600.0 * sun + own_light + room.gauss(0.0, 0.5)
        rms_mean = 4000.0 + 800.0 * room.random()
        returned = {"lux": lux, "rms_mean": rms_mean, "rms_max": rms_mean + 1500.0 * room.random(),
                    "vox": _heard(voice, room, muffle=0.1 if muffled else 1.0) if voice else None}
        action = {"rgbw": u, "voice": voice}

        out["arousal"].append(float(signal.get("A", 0.0) or 0.0))
        out["c1"].append(field.cells[1].activation)
        out["c7"].append(field.cells[7].activation)
        out["events"].append(bool(field.last_events))
        if result is not None and result["sound"] and result["sound"].get("voiced"):
            sound = result["sound"]
            size = max(abs(sound["excess"]), abs(sound["pred"]), 1e-6)
            out["tones"][-1].update(feel=sound.get("feel", 0.0),
                                    miss=min(1.0, abs(sound["err"]) / size))
        if voice:
            out["tones"].append({"tick": t, "freq": voice["freq"], "probe": probe_tone,
                                 "muffled": muffled, "feel": None, "miss": None,
                                 "since": t - previous_voice})
        out["feel_light"].append((result or {}).get("feel", {}).get("light", 0.0))
        out["probe_light"].append(probe["light"] if probe else 0.0)
        out["hidden"].append(covered)
        out["full_light"].append(model.light.lux_at_full())

    cf.LOOP_FEEL_GAIN = saved_gain
    out["model"] = model
    out["ring"] = ring_metrics(field)
    out["curiosity"] = curiosity
    return out


def _rise_after_tones(track, tones, span=3):
    """How much a per-tick track rises over the few ticks after each tone is
    heard, against the few ticks before it was sent."""
    rises = []
    for tone in tones:
        t = tone["tick"]
        before = track[max(0, t - span):t]
        after = track[t + 1:t + 1 + span]
        if before and after:
            rises.append(statistics.mean(after) - statistics.mean(before))
    return statistics.mean(rises) if rises else 0.0


def feel_probe(args):
    """The step-6 gate: the loop is felt. A learned echo presses on its loop
    cell faintly, a surprising one strongly, a loop that does not physically
    exist is not felt at all, and none of it runs away."""
    apply_overrides(args.set)
    ticks = args.ticks
    muffle = (int(ticks * 0.6), int(ticks * 0.7))

    def life():
        return scenario_inputs("day", ticks, random.Random(args.seed + 1))

    control = _run_body(args.seed, ticks, life(), FORWARD_COUPLING, feel=False, probes=False,
                        muffle=muffle, daylight=True)
    variant = _run_body(args.seed, ticks, life(), FORWARD_COUPLING, feel=True, probes=False,
                        muffle=muffle, daylight=True)
    blind = _run_body(args.seed, ticks, life(), (0.0, 0.0, 0.0, 0.0), feel=True, probes=False,
                      daylight=True)
    blind_control = _run_body(args.seed, ticks, life(), (0.0, 0.0, 0.0, 0.0), feel=False,
                              probes=False, daylight=True)

    def tones(run_, muffled, settled=True):
        return [x for x in run_["tones"] if x["feel"] is not None and x["muffled"] == muffled
                and (not settled or x["tick"] > ticks * 0.25)]

    plain = tones(variant, False)
    # The first tones after the cover goes on, before it has got used to it.
    covered = tones(variant, True)[:5]
    feel_plain = statistics.mean(x["feel"] for x in plain) if plain else 0.0
    feel_covered = statistics.mean(x["feel"] for x in covered) if covered else 0.0
    c1_variant = _rise_after_tones(variant["c1"], plain)
    c1_control = _rise_after_tones(control["c1"], tones(control, False))

    def saturated(run_):
        tail = run_["arousal"][ticks // 2:]
        return sum(1 for a in tail if a > 0.99) / len(tail)
    half = ticks // 2
    c7_blind = statistics.mean(blind["c7"][half:])
    c7_blind_control = statistics.mean(blind_control["c7"][half:])
    light_feel_blind = statistics.mean(blind["feel_light"][half:])
    light_feel_seen = statistics.mean(variant["feel_light"][half:])

    print(f"field {cf.FIELD_VERSION} | THE LOOP IS FELT | seed={args.seed} | ticks={ticks}")
    print(f"\n  voice: {len(plain)} tones heard normally, felt {feel_plain:.3f} on average; "
          f"the first {len(covered)} with the speaker covered, felt {feel_covered:.3f}")
    print(f"  speaker x sound cell, rise after a tone: {c1_variant:+.4f} (loop felt) vs "
          f"{c1_control:+.4f} (control)")
    print(f"  light: felt {light_feel_seen:.4f} per tick when the sensor sees the strip, "
          f"{light_feel_blind:.4f} when the sensor is blind")
    print(f"  led x light cell, blind sensor: {c7_blind:.4f} (loop felt) vs "
          f"{c7_blind_control:.4f} (control)")
    mean_variant = statistics.mean(variant["arousal"][half:])
    mean_control = statistics.mean(control["arousal"][half:])
    print(f"  tones: {len(variant['tones'])} (loop felt) vs {len(control['tones'])} (control); "
          f"mean arousal {mean_variant:.3f} vs {mean_control:.3f}; "
          f"at full arousal {saturated(variant) * 100:.1f}% vs {saturated(control) * 100:.1f}% of ticks")
    print(f"  differentiation: {variant['ring']['differentiation']:.4f} vs "
          f"{control['ring']['differentiation']:.4f}")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("a learned echo is felt, faintly",
          0.05 < feel_plain < 0.6,
          f"felt {feel_plain:.3f} of 1 on {len(plain)} ordinary tones")
    check("a covered speaker is felt strongly",
          len(covered) >= 3 and feel_covered > 1.5 * feel_plain,
          f"felt {feel_covered:.3f} on the first {len(covered)} muffled tones vs {feel_plain:.3f}")
    # The cell rises after a tone in the control too, since the voice speaks
    # just after a surprise. What is tested is what the felt echo adds to that.
    check("the loop cell answers its own voice",
          c1_variant > c1_control + 0.02,
          f"speaker x sound cell rises {c1_variant:+.4f} after a tone vs "
          f"{c1_control:+.4f} in control")
    check("a strip the sensor can see is felt",
          light_feel_seen > 0.05,
          f"felt {light_feel_seen:.3f} per tick seeing its own light")
    check("no loop, nothing felt",
          light_feel_blind < 0.02 and abs(c7_blind - c7_blind_control) <= 0.15 * max(c7_blind_control, 1e-6),
          f"blind sensor: light felt {light_feel_blind:.4f}, led x light cell "
          f"{c7_blind:.4f} vs {c7_blind_control:.4f} in control")
    check("it does not run away",
          mean_variant <= 1.25 * mean_control
          and saturated(variant) <= saturated(control) + 0.05
          and len(variant["tones"]) <= 1.5 * max(1, len(control["tones"])),
          f"mean arousal {mean_variant:.3f} vs {mean_control:.3f}, at full arousal "
          f"{saturated(variant) * 100:.1f}% vs {saturated(control) * 100:.1f}% of ticks, "
          f"{len(variant['tones'])} tones vs {len(control['tones'])}")
    check("structure holds",
          variant["ring"]["differentiation"] >= 0.7 * control["ring"]["differentiation"],
          f"differentiation {variant['ring']['differentiation']:.4f} vs "
          f"{control['ring']['differentiation']:.4f} in control")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


def curious_probe(args):
    """The step-7 gate: alone in a still dark room, the creature that may probe
    keeps finding out about itself, at a pace it sets, without running away."""
    apply_overrides(args.set)
    ticks = args.ticks
    blind_eye = (0.0, 0.0, 0.0, 0.0)

    def still():
        return _still_night(ticks)

    hide = (int(ticks * 0.75), int(ticks * 0.75) + 900)
    control = _run_body(args.seed, ticks, still(), blind_eye, feel=False, probes=False)
    variant = _run_body(args.seed, ticks, still(), blind_eye, feel=True, probes=True)
    seeing = _run_body(args.seed, ticks, still(), FORWARD_COUPLING, feel=True, probes=True,
                       cover=hide)

    half = ticks // 2
    hours = ticks / 3600.0

    def rate(run_):
        return sum(run_["events"][half:]) / (ticks - half)

    probes = [x for x in variant["tones"] if x["probe"]]
    heard = [x for x in variant["tones"] if x["miss"] is not None]
    third = max(1, len(heard) // 3)
    early = statistics.mean(x["miss"] for x in heard[:8]) if heard else 1.0
    late = statistics.mean(x["miss"] for x in heard[-third:]) if heard else 1.0
    probe_gap = min((x["since"] for x in probes), default=0)
    late_tones = [x for x in variant["tones"] if x["tick"] >= half]
    control_late_tones = [x for x in control["tones"] if x["tick"] >= half]
    bands = len({pitch_bin(x["freq"]) for x in variant["tones"]})
    truth = sum(FORWARD_COUPLING)
    blind_lux = variant["model"].light.lux_at_full()
    # What the seeing creature learned before its strip was hidden.
    seeing_lux = statistics.mean(seeing["full_light"][hide[0] - 300:hide[0]])

    # The seeing creature feels its own light all the time. Hide the strip and
    # that should fade as the model stops crediting itself with it.
    felt_before = statistics.mean(seeing["feel_light"][hide[0] - 300:hide[0]])
    felt_hidden = statistics.mean(seeing["feel_light"][hide[1] - 300:hide[1]])
    seeing_tail = seeing["arousal"][half:hide[0]]
    seeing_pinned = sum(1 for a in seeing_tail if a > 0.99) / len(seeing_tail)
    probe_feel = statistics.mean(x["feel"] for x in probes if x["feel"] is not None) if probes else 0.0
    variant_events = sum(variant["events"][half:])

    print(f"field {cf.FIELD_VERSION} | CURIOSITY | seed={args.seed} | ticks={ticks} "
          f"(a still dark room)")
    print("\n                      events/tick   mean arousal   peak   tones per hour")
    for name, run_ in (("no loop, no probes        ", control),
                       ("loop felt + probes        ", variant),
                       ("the same, sensor sees strip", seeing)):
        tail_tones = [x for x in run_["tones"] if x["tick"] >= half]
        print(f"    {name}  {rate(run_) * 100:7.2f}%      {statistics.mean(run_['arousal'][half:]):.3f}"
              f"        {max(run_['arousal'][half:]):.3f}     {len(tail_tones) / (hours / 2):5.1f}")
    print(f"\n  probe tones: {len(probes)}, across {bands} of {SOUND_PITCH_BINS} bands of its voice, "
          f"never sooner than {probe_gap} ticks after the tone before")
    print(f"  how far off it was about its own voice: {early * 100:.0f}% on its first tones, "
          f"{late * 100:.0f}% on its last")
    print(f"  light it credits to itself at full: {blind_lux:.1f} lux with a blind sensor, "
          f"{seeing_lux:.1f} with one that sees the strip (room has {truth:.0f})")
    print(f"  its own light, felt per tick: {felt_before:.3f} while the sensor sees the strip, "
          f"{felt_hidden:.3f} after 15 minutes with it hidden")
    print(f"  seeing its own light: mean arousal {statistics.mean(seeing_tail):.3f}, "
          f"at full arousal {seeing_pinned * 100:.1f}% of ticks")

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("alone and unprobed, nothing surprises it",
          rate(control) < 0.002,
          f"once settled: events on {rate(control) * 100:.2f}% of ticks "
          f"(it still voices {len(control_late_tones)} tones of its own)")
    check("bored, it probes, and feels the answer",
          len(probes) >= 5 and probe_feel > 0.1 and variant_events >= 5
          and variant_events > sum(control["events"][half:]),
          f"{len(probes)} probe tones, felt {probe_feel:.3f} on average, "
          f"{variant_events} events once settled against {sum(control['events'][half:])}")
    check("the probing is paced",
          len(late_tones) / (hours / 2) <= 15.0 and probe_gap >= 120,
          f"{len(late_tones) / (hours / 2):.1f} tones an hour once settled, no probe sooner "
          f"than {probe_gap} ticks after the last tone")
    check("it explores its whole voice",
          bands >= 6,
          f"{bands} of {SOUND_PITCH_BINS} bands tried")
    # Judged against predicting nothing, not against its own first tones: how
    # wrong those are depends on which pitches it happens to try first.
    voice_explained = variant["model"].sound.explained()
    check("it learns its own voice",
          voice_explained > 0.6 and late < 0.25,
          f"explains {voice_explained:.3f} of what it hears of itself; off by "
          f"{early * 100:.0f}% on its first tones, {late * 100:.0f}% on its last")
    check("it does not run away",
          max(variant["arousal"][half:]) < 0.99,
          f"peak arousal {max(variant['arousal'][half:]):.3f}")
    check("the light probe finds its own light only where there is some",
          abs(blind_lux) < 0.1 * truth and abs(seeing_lux - truth) < 0.25 * truth,
          f"{blind_lux:.1f} lux credited with a blind sensor, {seeing_lux:.1f} of {truth:.0f} "
          f"with a seeing one")
    check("seeing its own light keeps it lively without pinning it",
          statistics.mean(seeing_tail) > 1.5 * statistics.mean(variant["arousal"][half:])
          and seeing_pinned < 0.1,
          f"mean arousal {statistics.mean(seeing_tail):.3f} against "
          f"{statistics.mean(variant['arousal'][half:]):.3f} blind, at full arousal "
          f"{seeing_pinned * 100:.1f}% of ticks")
    check("when its light stops coming back, the feeling fades",
          felt_before > 0.05 and felt_hidden < 0.5 * felt_before,
          f"felt {felt_before:.3f} per tick seeing the strip, {felt_hidden:.3f} after "
          f"15 minutes with it hidden")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# When the voice speaks (v06.9)
# ---------------------------------------------------------------------------
#
# The fixed rule voices a tone whenever arousal is at or above 0.45. On the
# twelve-cell body that is true about half the time, so with a 20-second minimum
# between tones the voice ran as a metronome: over 1.8 hours on 2 October 2026 the
# live creature sent 242 tones, 192 of them exactly at the minimum spacing.
#
# The relative rule speaks when arousal stands clear of its own usual level,
# something has just surprised the field, and it has come back down since it last
# spoke. Control (fixed) against variant (relative), same seed, same input. With
# --replay it runs both on the creature's own recorded senses instead.

def _voice_run(model, inputs, seed, state_path=None):
    """One life under one voice rule. Tones respect the collector's 20-tick
    minimum spacing. Returns tone ticks and per-tick arousal and events."""
    random.seed(seed)
    field = cf.build_field()
    if state_path:
        cf.load_field(field, state_path)
    decoder = ExpressionDecoderV06(knobs={"VOICE_MODEL": model})
    tones, arousal, events = [], [], []
    last = -FORWARD_VOICE_GAP
    for t, values in enumerate(inputs):
        state = field.step(values)
        signal = decoder.read(state)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        arousal.append(max(float(signal.get("A", 0.0) or 0.0), speaker))
        events.append(bool(field.last_events))
        if voice_params_from_signal(signal, speaker) and t - last >= FORWARD_VOICE_GAP:
            tones.append(t)
            last = t
    return {"tones": tones, "arousal": arousal, "events": events, "ticks": len(arousal)}


def _voice_stats(run_, skip=0):
    tones = [t for t in run_["tones"] if t >= skip]
    hours = (run_["ticks"] - skip) / 3600.0
    gaps = [b - a for a, b in zip(tones, tones[1:])]
    back = sum(1 for g in gaps if g <= FORWARD_VOICE_GAP + 4) / len(gaps) if gaps else 0.0
    at_tone = statistics.mean(run_["arousal"][t] for t in tones) if tones else 0.0
    return {
        "per_hour": len(tones) / hours if hours else 0.0,
        "back_to_back": back,
        "arousal_at_tone": at_tone,
        "arousal": statistics.mean(run_["arousal"][skip:]),
        "count": len(tones),
    }


def _voice_line(name, st):
    return (f"    {name:10s} {st['per_hour']:6.1f} tones an hour | "
            f"{st['back_to_back'] * 100:3.0f}% at the minimum spacing | arousal "
            f"{st['arousal_at_tone']:.2f} when it speaks, {st['arousal']:.2f} on average")


def voice_probe(args):
    """The voice gate: the fixed rule is a metronome, the relative rule speaks far
    less, at moments that stand out, never back to back, not at all in a still
    room, and still answers a loud regular world."""
    apply_overrides(args.set)
    print(f"field {cf.FIELD_VERSION} | WHEN THE VOICE SPEAKS | seed={args.seed}")

    if args.replay:
        rows = _read_replay(args.replay)
        print(f"\n  replay of {len(rows)} recorded ticks"
              + (f", starting from {args.state}" if args.state else ""))
        for model in ("fixed", "relative"):
            print(_voice_line(model, _voice_stats(_voice_run(model, rows, args.seed, args.state))))
        return True

    ticks = args.ticks
    settle = 600   # past the decoder's warm-up and the field's first minutes

    def run(model, name):
        if name == "still":
            inputs = _still_night(ticks)
        else:
            inputs = scenario_inputs(name, ticks, random.Random(args.seed + 1))
        return _voice_run(model, inputs, args.seed)

    stats = {}
    runs = {}
    for model in ("fixed", "relative"):
        print(f"\n  {model} rule" + (" (control)" if model == "fixed" else " (variant)"))
        for name in ("day", "bursts", "still"):
            runs[(model, name)] = run(model, name)
            stats[(model, name)] = _voice_stats(runs[(model, name)], settle)
            print(_voice_line(name, stats[(model, name)]))

    # A burst starts every 40 ticks. How many does the relative rule answer?
    burst_tones = set(runs[("relative", "bursts")]["tones"])
    onsets = list(range(settle - settle % 40 + 40, ticks - 6, 40))
    answered = sum(1 for t in onsets if any((t + k) in burst_tones for k in range(6))) / len(onsets)

    fixed_day, rel_day = stats[("fixed", "day")], stats[("relative", "day")]
    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("the fixed rule is a metronome",
          fixed_day["back_to_back"] > 0.5,
          f"{fixed_day['per_hour']:.0f} tones an hour, {fixed_day['back_to_back'] * 100:.0f}% "
          f"of them at the minimum spacing")
    check("the relative rule speaks far less",
          1.0 <= rel_day["per_hour"] <= 0.5 * fixed_day["per_hour"],
          f"{rel_day['per_hour']:.0f} tones an hour against {fixed_day['per_hour']:.0f}")
    check("never back to back",
          rel_day["back_to_back"] < 0.1,
          f"{rel_day['back_to_back'] * 100:.0f}% of its tones at the minimum spacing")
    check("it speaks at moments that stand out",
          rel_day["arousal_at_tone"] > rel_day["arousal"] + 0.1,
          f"arousal {rel_day['arousal_at_tone']:.2f} when it speaks, "
          f"{rel_day['arousal']:.2f} on average")
    check("silent in a still room",
          stats[("relative", "still")]["count"] == 0,
          f"{stats[('relative', 'still')]['count']} tones once settled "
          f"(the fixed rule: {stats[('fixed', 'still')]['count']})")
    check("a loud regular world is still answered",
          answered >= 0.5,
          f"it speaks at {answered * 100:.0f}% of {len(onsets)} bursts")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# How much of its range the expression uses (after v06.9)
# ---------------------------------------------------------------------------
#
# On the body of 3 October 2026 the strip was always bright and the voice kept
# to a few pitches of one length. Tempo sat at its maximum on every sample:
# since the light sensor began to see the strip, the field's usual ripple is
# about three times RIPPLE_REF. Balance kept to a narrow warm band, and pitch
# comes from balance alone.
#
# Control (fixed references) against variant (the field's own usual levels),
# same seed, same room. The room here can close the light loop the way the body
# does: the strip's own light reaches the sensor and goes through the same
# rolling normalizer the collector uses, so a brighter strip is a busier light
# sense. With --replay it runs both on recorded senses instead (open loop: what
# was recorded cannot answer a changed strip).

EXPRESS_LUX_FULL = 200.0      # lux the sensor gets from the strip at full, as the body's model reports
EXPRESS_LUX_CLOSE = 400.0     # the same with twice the coupling; the body's own figure is not settled
EXPRESS_SENSOR_PIXEL = 10     # the sensor sits beside this part of the strip
EXPRESS_SENSOR_WIDTH = 2.5    # pixels
# The collector's light normalizer settings (collector.py LIGHT_*).
EXPRESS_LIGHT_WINDOW = 120.0
EXPRESS_LIGHT_ALPHA = 0.2
EXPRESS_LIGHT_MIN_RANGE = 50.0


def _strip_lux(pixels, full, cap=200.0):
    """Lux the strip adds at a sensor sitting beside it. Nearby pixels count most."""
    total = weight = 0.0
    for p, (r, g, b, w) in enumerate(pixels):
        wt = math.exp(-((p - EXPRESS_SENSOR_PIXEL) / EXPRESS_SENSOR_WIDTH) ** 2)
        total += wt * ((r + g + b) / 3.0 + w) / (2.0 * cap)
        weight += wt
    return full * total / weight if weight else 0.0


def _express_run(model, inputs, seed, seen=0.0, state_path=None, restart_at=None):
    """One life under one expression model. `seen` is the lux the strip puts on
    the sensor at full (0: the sensor cannot see it). With it, the room's light
    is turned into lux, the strip's own light is added, and the sum is normalized
    as the collector does, so the field senses its own strip. Tones respect the
    collector's 20-tick minimum spacing. At `restart_at` the collector is
    restarted: the field is saved and loaded again, and the decoder and the
    light normalizer start fresh. Returns per-tick tracks."""
    random.seed(seed)
    room = random.Random(seed + 2)
    field = cf.build_field()
    if state_path:
        cf.load_field(field, state_path)
    decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": model})
    light = RollingNormalizer(EXPRESS_LIGHT_WINDOW, EXPRESS_LIGHT_ALPHA, EXPRESS_LIGHT_MIN_RANGE)
    out = {"A": [], "B": [], "T": [], "level": [], "tones": [], "asleep": [], "light": []}
    own = 0.0
    last = -FORWARD_VOICE_GAP
    for t, values in enumerate(inputs):
        if seen:
            lux = 5.0 + 600.0 * values["light"] + own + room.gauss(0.0, 0.5)
            for i in range(10):     # the body samples at about 10 Hz
                light.add(lux, t + i / 10.0)
            values = dict(values, light=light.normalized())
        if t == restart_at:
            with tempfile.TemporaryDirectory() as folder:
                saved = str(Path(folder) / "field.json")
                cf.save_field(field, saved)
                field = cf.build_field()
                cf.load_field(field, saved)
            decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": model})
            light = RollingNormalizer(EXPRESS_LIGHT_WINDOW, EXPRESS_LIGHT_ALPHA, EXPRESS_LIGHT_MIN_RANGE)
        state = field.step(values)
        signal = decoder.read(state)
        pixels = signal["pixels"]
        own = _strip_lux(pixels, seen) if seen else 0.0
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        voice = voice_params_from_signal(signal, speaker)
        if voice and t - last >= FORWARD_VOICE_GAP:
            out["tones"].append({"tick": t, "freq": voice["freq"], "ms": voice["ms"]})
            last = t
        out["A"].append(signal["A"])
        out["B"].append(signal["B"])
        out["T"].append(signal["T"])
        out["level"].append(sum(r + g + b + w for r, g, b, w in pixels) / (len(pixels) * 4.0 * 200.0))
        out["asleep"].append(state["metabolism"]["mode"] == "sleep")
        out["light"].append(values["light"])
    return out


def _pct(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else 0.0


def _express_stats(run_, skip=0):
    tempo = run_["T"][skip:]
    level = run_["level"][skip:]
    balance = run_["B"][skip:]
    tones = [tone for tone in run_["tones"] if tone["tick"] >= skip]
    notes = [12.0 * math.log2(tone["freq"] / 220.0) for tone in tones]
    return {
        "pinned": sum(1 for v in tempo if v >= 0.999) / len(tempo),
        "tempo_span": _pct(tempo, 0.95) - _pct(tempo, 0.05),
        "level_low": _pct(level, 0.05),
        "level_mid": _pct(level, 0.50),
        "level_high": _pct(level, 0.95),
        "balance_span": _pct(balance, 0.95) - _pct(balance, 0.05),
        "tones": len(tones),
        "pitch_span": (_pct(notes, 0.95) - _pct(notes, 0.05)) if notes else 0.0,
        "lengths": len({tone["ms"] // 20 for tone in tones}),
        "asleep": sum(run_["asleep"][skip:]) / len(tempo),
        "light_busy": sum(1 for v in run_["light"][skip:] if v > 0.0) / len(tempo),
    }


def _express_line(name, st):
    return (f"    {name:17s} tempo at max {st['pinned'] * 100:3.0f}% of the time | strip level "
            f"{st['level_low']:.2f} / {st['level_mid']:.2f} / {st['level_high']:.2f} (low / usual / high) | "
            f"{st['tones']:3d} tones over {st['pitch_span']:4.1f} semitones, "
            f"{st['lengths']} lengths | asleep {st['asleep'] * 100:3.0f}%")


def _nightfall(ticks, seed):
    """Half a busy day, then a dark and silent room. After dark the only light
    that moves is the creature's own, and it arrives there already stirred up."""
    day = scenario_inputs("day", ticks // 2, random.Random(seed + 1))
    for values in day:
        yield values
    for values in _still_night(ticks - ticks // 2):
        yield values


def express_probe(args):
    """The expression gate: under fixed references a body that senses its own
    strip holds tempo at its maximum; measured against its own usual, tempo,
    brightness, pitch and tone length each use more of their range, and a calm
    field is left as it was."""
    apply_overrides(args.set)
    print(f"field {cf.FIELD_VERSION} | HOW MUCH OF ITS RANGE THE EXPRESSION USES | seed={args.seed}")

    if args.replay:
        rows = _read_replay(args.replay)
        print(f"\n  replay of {len(rows)} recorded ticks"
              + (f", starting from {args.state}" if args.state else "") + " (open loop)")
        for model in ("fixed", "relative"):
            print(_express_line(model, _express_stats(
                _express_run(model, rows, args.seed, state_path=args.state), 600)))
        return True

    ticks = args.ticks
    settle = 1800   # past the warm-ups and the first settling of the running medians

    def inputs(name):
        if name == "still":
            return _still_night(ticks)
        if name.startswith("after dark"):
            return _nightfall(ticks, args.seed)
        return scenario_inputs("day", ticks, random.Random(args.seed + 1))

    # "after dark" is judged on the last quarter of the run, well into the night.
    night = ticks - ticks // 4
    rooms = (("day seen", EXPRESS_LUX_FULL, settle), ("day unseen", 0.0, settle),
             ("after dark", EXPRESS_LUX_FULL, night), ("after dark, close", EXPRESS_LUX_CLOSE, night),
             ("still", 0.0, settle))
    stats = {}
    for model in ("fixed", "relative"):
        print(f"\n  {model} model" + (" (control)" if model == "fixed" else " (variant)"))
        for name, seen, skip in rooms:
            stats[(model, name)] = _express_stats(
                _express_run(model, inputs(name), args.seed, seen=seen), skip)
            print(_express_line(name, stats[(model, name)]))

    # A restart in the middle of a busy day, judged on the five minutes that
    # start two minutes after it.
    restart = 2000
    woken = _express_run("relative", inputs("day seen"), args.seed, seen=EXPRESS_LUX_FULL,
                         restart_at=restart)
    woken_tempo = woken["T"][restart + 120:restart + 420]
    woken_pinned = sum(1 for v in woken_tempo if v >= 0.999) / len(woken_tempo)

    fixed, rel = stats[("fixed", "day seen")], stats[("relative", "day seen")]
    fixed_dark, rel_dark = stats[("fixed", "after dark")], stats[("relative", "after dark")]
    fixed_close, rel_close = stats[("fixed", "after dark, close")], stats[("relative", "after dark, close")]
    fixed_still, rel_still = stats[("fixed", "still")], stats[("relative", "still")]

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("the fixed model holds tempo at its maximum",
          fixed["pinned"] > 0.5,
          f"at its maximum {fixed['pinned'] * 100:.0f}% of a day with the strip seen")
    check("the relative model does not",
          rel["pinned"] < 0.10 and rel["tempo_span"] >= 0.4,
          f"at its maximum {rel['pinned'] * 100:.0f}% of the time, "
          f"and tempo ranges over {rel['tempo_span']:.2f} of its scale")
    check("it finds its level within minutes of a restart",
          woken_pinned < 0.20,
          f"tempo at its maximum {woken_pinned * 100:.0f}% of the time from two to seven "
          f"minutes after a restart in a busy day")
    check("the strip rests dimmer and still reaches its peaks",
          rel["level_mid"] <= 0.7 * fixed["level_mid"] and rel["level_high"] >= 0.7 * fixed["level_high"],
          f"usual level {rel['level_mid']:.2f} against {fixed['level_mid']:.2f}, "
          f"high level {rel['level_high']:.2f} against {fixed['level_high']:.2f}")
    check("the voice uses more pitches",
          rel["tones"] >= 10 and rel["pitch_span"] >= 6.0 and rel["pitch_span"] >= 1.5 * fixed["pitch_span"],
          f"{rel['tones']} tones over {rel['pitch_span']:.1f} semitones against "
          f"{fixed['tones']} over {fixed['pitch_span']:.1f}")
    check("and more than one length of tone",
          rel["lengths"] >= 3,
          f"{rel['lengths']} lengths against {fixed['lengths']}")
    check("after dark, lit only by its own strip, it settles",
          rel_dark["pinned"] < 0.10 and rel_dark["level_mid"] <= 0.5 * fixed_dark["level_mid"],
          f"tempo at its maximum {rel_dark['pinned'] * 100:.0f}% of the time against "
          f"{fixed_dark['pinned'] * 100:.0f}%, usual level {rel_dark['level_mid']:.2f} "
          f"against {fixed_dark['level_mid']:.2f}, the light sense busy "
          f"{rel_dark['light_busy'] * 100:.0f}% of the time against {fixed_dark['light_busy'] * 100:.0f}%")
    check("and still settles with twice the strip on the sensor",
          rel_close["pinned"] < 0.10 and rel_close["level_mid"] <= 0.5 * fixed_close["level_mid"],
          f"tempo at its maximum {rel_close['pinned'] * 100:.0f}% of the time against "
          f"{fixed_close['pinned'] * 100:.0f}%, usual level {rel_close['level_mid']:.2f} "
          f"against {fixed_close['level_mid']:.2f}, the light sense busy "
          f"{rel_close['light_busy'] * 100:.0f}% of the time against {fixed_close['light_busy'] * 100:.0f}%")
    check("a still room is left as it was",
          abs(rel_still["level_mid"] - fixed_still["level_mid"]) <= 0.01
          and rel_still["tones"] == 0
          and abs(rel_still["asleep"] - fixed_still["asleep"]) <= 0.02,
          f"usual level {rel_still['level_mid']:.3f} against {fixed_still['level_mid']:.3f}, "
          f"{rel_still['tones']} tones, asleep {rel_still['asleep'] * 100:.0f}% "
          f"against {fixed_still['asleep'] * 100:.0f}%")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# Significant events: what gets remembered for sleep (v06.8)
# ---------------------------------------------------------------------------
#
# Sleep replays the field's "significant events". Under the pressure rule the
# weather anchor's steady pressure crossed the fixed threshold on every tick, so
# every tick was an event, the 80-slot buffer only ever held the last 80 seconds,
# and sleep replayed the quiet run-up to itself. The surprise rule scores a tick
# by its most surprised cell and fires only when that stands clear of the field's
# own running baseline.
#
# Control (pressure) against variant (surprise), same seed, same input. With
# --replay it runs both on the creature's own recorded senses instead.

EVENT_WORLD_DELTA = 0.05     # a sense moved this much: the world did something


def _read_replay(path):
    """Recorded senses, one row per tick: tick, logged_at, sound, light, motion,
    weather (extra columns ignored). Export from the Pi with:

        sqlite3 -readonly -csv creature_raw_light.db "select tick, logged_at,
          sound_norm, light_norm, motion_norm, weather_norm from field_tick_log
          order by id" > senses.csv
    """
    rows = []
    with open(path) as src:
        for line in src:
            parts = line.strip().split(",")
            if len(parts) < 6:
                continue
            try:
                rows.append({
                    "sound": float(parts[2] or 0.0),
                    "light": float(parts[3] or 0.0),
                    "motion": float(parts[4] or 0.0),
                    "weather": float(parts[5] or 0.0),
                })
            except ValueError:
                continue
    return rows


def _still_night(ticks):
    """A night as the live creature gets it: the fast senses read exactly zero
    (the normalizer gates a still room to 0) and only the weather drifts. The
    'quiet' scenario keeps a little jitter on every sense, which is enough to
    stop the low-stimulation sleep from ever arming, so sleep needs this one."""
    for t in range(ticks):
        yield {"sound": 0.0, "light": 0.0, "motion": 0.0,
               "weather": 0.45 + 0.05 * math.sin(t / 7200 * 2 * math.pi)}


def _event_run(model, phases, seed, state_path=None):
    """Run one life under one event rule. `phases` is a list of (name, inputs).
    Returns the field and per-phase counts."""
    cf.EVENT_MODEL = model
    random.seed(seed)
    field = cf.build_field()
    if state_path:
        cf.load_field(field, state_path)
    out = {}
    recent = [0.0, 0.0, 0.0]
    last = None
    was_sleeping = False
    for name, inputs in phases:
        st = {"ticks": 0, "events": 0, "world": 0, "top": {}, "sleeps": 0,
              "reviewed": 0, "reinforced": 0, "fired": []}
        for values in inputs:
            field.step(values)
            delta = 0.0 if last is None else max(
                abs(values[k] - last[k]) for k in cf.SENSES)
            last = values
            recent = recent[1:] + [delta]
            st["ticks"] += 1
            fired = bool(field.last_events)
            st["fired"].append(fired)
            for event in field.last_events:
                st["events"] += 1
                if max(recent) >= EVENT_WORLD_DELTA:
                    st["world"] += 1
                n = event["cells"][0]["n"]
                st["top"][n] = st["top"].get(n, 0) + 1
            if field.sleep_mode == "sleep" and not was_sleeping:
                st["sleeps"] += 1
            was_sleeping = field.sleep_mode == "sleep"
            summary = field.last_sleep_summary
            if summary:
                st["reviewed"] += summary.get("events_reviewed", 0) or 0
                st["reinforced"] += summary.get("links_reinforced", 0) or 0
        st["ring"] = ring_metrics(field)
        out[name] = st
    return field, out


def _event_line(name, st):
    rate = st["events"] / st["ticks"] if st["ticks"] else 0.0
    world = st["world"] / st["events"] if st["events"] else 0.0
    top = sorted(st["top"].items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_text = ", ".join(f"{cf.RING[n][0]} {c / st['events'] * 100:.0f}%" for n, c in top)
    return (f"    {name:12s} events on {rate * 100:6.2f}% of ticks | "
            f"{world * 100:5.1f}% with a sense change | sleeps {st['sleeps']:3d}, "
            f"{st['reviewed']:5d} replayed | top: {top_text}")


def events_probe(args):
    """The significant-event gate: the pressure rule marks every tick, the
    surprise rule stays quiet in a quiet room, still catches real changes, points
    at the cell that changed, and leaves sleep and structure working."""
    apply_overrides(args.set)
    saved_model = cf.EVENT_MODEL
    print(f"field {cf.FIELD_VERSION} | SIGNIFICANT EVENTS | seed={args.seed}")

    if args.replay:
        rows = _read_replay(args.replay)
        print(f"\n  replay of {len(rows)} recorded ticks"
              + (f", starting from {args.state}" if args.state else ""))
        for model in ("pressure", "surprise"):
            _field, out = _event_run(model, [("recorded", rows)], args.seed, args.state)
            st = out["recorded"]
            print(_event_line(model, st))
            ring = st["ring"]
            print(f"      replayed {st['reviewed']} events, reinforced {st['reinforced']} links"
                  f" | differentiation {ring['differentiation']:.4f}"
                  f" | loop cells {ring['loop_cells_mean']:.3f}"
                  f" correlated {ring['correlated_cells_mean']:.3f}"
                  f" weak gap {ring['weak_gap_mean']:.3f}")
        cf.EVENT_MODEL = saved_model
        return True

    def day_night(model):
        rng = random.Random(args.seed + 1)
        phases = [("day", list(scenario_inputs("day", args.ticks, rng))),
                  ("night", list(_still_night(args.night)))]
        return _event_run(model, phases, args.seed)[1]

    def bursts(model):
        rng = random.Random(args.seed + 1)
        return _event_run(model, [("bursts", list(scenario_inputs("bursts", args.ticks, rng)))],
                          args.seed)[1]["bursts"]

    control = day_night("pressure")
    variant = day_night("surprise")
    control_b = bursts("pressure")
    variant_b = bursts("surprise")
    cf.EVENT_MODEL = saved_model

    print("\n  pressure rule (control)")
    for name in ("day", "night"):
        print(_event_line(name, control[name]))
    print(_event_line("bursts", control_b))
    print("  surprise rule (variant)")
    for name in ("day", "night"):
        print(_event_line(name, variant[name]))
    print(_event_line("bursts", variant_b))

    # A burst starts every 40 ticks. Skip the first few while the baseline settles.
    onsets = [t for t in range(400, args.ticks - 3, 40)]
    caught = sum(1 for t in onsets if any(variant_b["fired"][t:t + 3])) / len(onsets)
    weather = cf.SENSE_ANCHOR["weather"]
    weather_share = variant_b["top"].get(weather, 0) / max(1, variant_b["events"])
    control_weather = control["night"]["top"].get(weather, 0) / max(1, control["night"]["events"])

    def rate(st):
        return st["events"] / st["ticks"]

    def held(run_):
        day, night = run_["day"]["ring"], run_["night"]["ring"]
        return night["differentiation"] / day["differentiation"] if day["differentiation"] else 0.0

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("the pressure rule marks nearly every quiet tick",
          rate(control["night"]) > 0.9,
          f"events on {rate(control['night']) * 100:.1f}% of still-night ticks, "
          f"{control_weather * 100:.0f}% of them topped by the weather anchor")
    check("the surprise rule stays quiet in a quiet room",
          rate(variant["night"]) < 0.02,
          f"events on {rate(variant['night']) * 100:.2f}% of still-night ticks")
    check("real changes are still caught",
          caught >= 0.8,
          f"{caught * 100:.0f}% of {len(onsets)} burst onsets get an event within 2 ticks")
    check("events point at what changed, not at the steady anchor",
          weather_share < 0.05,
          f"weather anchor tops {weather_share * 100:.1f}% of burst events")
    total_sleeps = variant["day"]["sleeps"] + variant["night"]["sleeps"]
    total_reviewed = variant["day"]["reviewed"] + variant["night"]["reviewed"]
    check("sleep still consolidates",
          total_sleeps >= 1 and total_reviewed >= 1,
          f"{total_sleeps} sleeps, {total_reviewed} events replayed "
          f"(control: {control['day']['sleeps'] + control['night']['sleeps']} sleeps, "
          f"{control['day']['reviewed'] + control['night']['reviewed']} replayed)")
    # Same bar as the ring gate. The control number is shown but is not the bar:
    # replaying its own background every sleep keeps adding weight at night.
    check("structure survives the night",
          held(variant) >= 0.60,
          f"differentiation held {held(variant) * 100:.0f}% through the night "
          f"(control {held(control) * 100:.0f}%, fed by replaying its own background)")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# How far back the memory reaches (after v06.9)
# ---------------------------------------------------------------------------
#
# The field learns, but what it learned does not last. On 3 October 2026 the
# nine-day-old field and a newborn one were fed the same recorded day. After 24
# hours every ring link of the newborn was within 0.1 of the elder's, and the two
# expressed alike to within what a changed random seed does.
#
# "Memory horizon" is how long a newborn needs, on the same input, to become
# indistinguishable from the elder. This gate measures it and changes nothing.
# Three lives on the same input, field and decoder only: the elder (a field that
# has already lived), a newborn (nothing loaded), and the elder again on the next
# seed, which sets the noise floor. With --replay and --state the elder is the
# saved field and the input is recorded senses. Without them the elder is raised
# here on the bursts scenario and all three get the same synthetic day.

HISTORY_TICKS_PER_HOUR = 3600
HISTORY_WEIGHT_EVERY = 600     # ticks between samples of the ring weights
HISTORY_NOISE_MIN = 0.005      # the noise gap is never taken as smaller than this
HISTORY_LINK_CLOSE = 0.1       # two weights this close count as the same
HISTORY_RAIL_TOP = 0.1         # a link within this of W_MAX is railed
HISTORY_RAIL_FLOOR = 0.005     # and so is one within this of PRUNE_FLOOR
HISTORY_STILL_HOURS = 60       # the elder's time in a still room, to see whether its links weaken
HISTORY_STILL_SETTLE = 24      # hours in before the weights are first read
HISTORY_BUILT = 0.5            # a link driving at this or more counts as one its life built
HISTORY_STILL_FALL = 0.01      # falling: the resting level this much lower, as a share of itself


def _history_life(inputs, seed, state_path=None):
    """One life on the test input. Returns A, B and T per tick, and the ring
    weights (sorted by link key) at the start and every HISTORY_WEIGHT_EVERY
    ticks after: `weights` are the links' own (fast) weights, `drive` the weights
    the neighbour drive uses, which differ only with two-speed links. The decoder
    is held to the fixed expression model: the relative one reads each field
    against its own usual, which would hide the difference this gate looks for."""
    random.seed(seed)
    field = cf.build_field()
    if state_path and cf.load_field(field, state_path) is None:
        raise SystemExit(f"could not load a field state from {state_path}")
    decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "fixed"})
    keys = sorted(field.weights)

    def drive():
        used = field.drive_weights()
        return [used[k] for k in keys]

    out = {"A": [], "B": [], "T": [], "keys": keys,
           "weights": [[field.weights[k] for k in keys]], "drive": [drive()]}
    for t, values in enumerate(inputs, 1):
        signal = decoder.read(field.step(values))
        out["A"].append(signal["A"])
        out["B"].append(signal["B"])
        out["T"].append(signal["T"])
        if t % HISTORY_WEIGHT_EVERY == 0:
            out["weights"].append([field.weights[k] for k in keys])
            out["drive"].append(drive())
    return out


def _history_elder(seed, hours, path):
    """Raise a field on the bursts scenario, then save it. Loaded again, it starts
    a test the way the collector's saved field does: structure kept, the moment
    lost."""
    random.seed(seed)
    raised = cf.build_field()
    for values in scenario_inputs("bursts", hours * HISTORY_TICKS_PER_HOUR, random.Random(seed + 1)):
        raised.step(values)
    cf.save_field(raised, path)


def _history_still(seed, state_path):
    """The elder in a still room: can the links its life built still weaken?

    What is watched is the level a link rests on once nothing drives it: the
    floor, or with two-speed links its slow weight, which decay pulls the fast
    one toward. Each built link's resting level must be the floor, or lower at
    the end than a day in. The weight the drive uses is not enough to go by: for
    days it is still sliding down to the resting level, and that looks like
    forgetting even when the resting level itself can never fall."""
    per = HISTORY_TICKS_PER_HOUR
    random.seed(seed)
    field = cf.build_field()
    if cf.load_field(field, state_path) is None:
        raise SystemExit(f"could not load a field state from {state_path}")
    keys = sorted(field.weights)

    def rest():
        if cf.SLOW_MIX > 0.0:
            return dict(field.slow_weights)
        return {k: cf.PRUNE_FLOOR for k in keys}

    drive = {0: dict(field.drive_weights())}
    resting = {}
    for t, values in enumerate(_still_night(HISTORY_STILL_HOURS * per), 1):
        field.step(values)
        if t in (HISTORY_STILL_SETTLE * per, HISTORY_STILL_HOURS * per):
            drive[t // per] = dict(field.drive_weights())
            resting[t // per] = rest()
    day, end = resting[HISTORY_STILL_SETTLE], resting[HISTORY_STILL_HOURS]
    built = [k for k in keys if drive[0][k] >= HISTORY_BUILT]
    held = [k for k in built
            if end[k] > cf.PRUNE_FLOOR + HISTORY_RAIL_FLOOR
            and end[k] > day[k] * (1.0 - HISTORY_STILL_FALL)]

    def mean(values):
        return round(statistics.mean(values[k] for k in built), 5) if built else None

    return {
        "built": len(built),
        "held_up": len(held),
        "weakens": bool(built) and not held,
        "two_speed": cf.SLOW_MIX > 0.0,
        "rest_day": mean(day), "rest_end": mean(end),
        "drive_start": mean(drive[0]), "drive_day": mean(drive[HISTORY_STILL_SETTLE]),
        "drive_end": mean(drive[HISTORY_STILL_HOURS]),
    }


def _history_still_text(still):
    if not still["built"]:
        return "the elder has no link driving at 0.5 or more to watch"
    text = (f"the {still['built']} links its life built drive at {still['drive_start']:.2f} on average, "
            f"{still['drive_day']:.2f} after {HISTORY_STILL_SETTLE} still hours and "
            f"{still['drive_end']:.2f} after {HISTORY_STILL_HOURS}; ")
    if not still["two_speed"]:
        return text + "they rest on the floor"
    return text + (f"they rest on slow weights of {still['rest_day']:.4f}, then {still['rest_end']:.4f}; "
                   f"{still['held_up']} of them not falling")


def _history_gap(a, b, name, lo, hi):
    return statistics.mean(abs(x - y) for x, y in zip(a[name][lo:hi], b[name][lo:hi]))


def _history_rows(elder, newborn, noise, hours):
    """One row per hour of the test."""
    per = HISTORY_TICKS_PER_HOUR
    samples = per // HISTORY_WEIGHT_EVERY
    rows = []
    for hour in range(hours):
        lo, hi = hour * per, (hour + 1) * per
        first, end = hour * samples, (hour + 1) * samples
        gap = {name: _history_gap(elder, newborn, name, lo, hi) for name in "ABT"}
        noise_gap = sum(_history_gap(elder, noise, name, lo, hi) for name in "AB")
        old, new = elder["weights"][end], newborn["weights"][end]

        def travelled(track):
            # How far the newborn's weights went this hour, sample to sample.
            return sum(abs(b - a)
                       for s in range(first, end)
                       for a, b in zip(newborn[track][s], newborn[track][s + 1]))

        moved = travelled("weights")
        rows.append({
            "hour": hour,
            "expression_gap": round(gap["A"] + gap["B"], 5),
            "noise_gap": round(noise_gap, 5),
            "link_gap": round(max(abs(a - b) for a, b in zip(old, new)), 5),
            "newborn_learning": round(moved, 5),
            "railed_links": sum(1 for w in old
                                if w >= cf.W_MAX - HISTORY_RAIL_TOP
                                or w <= cf.PRUNE_FLOOR + HISTORY_RAIL_FLOOR),
            "gap_a": round(gap["A"], 5),
            "gap_b": round(gap["B"], 5),
            "gap_t": round(gap["T"], 5),
            # The same two on the weights the drive uses (two-speed links).
            "drive_link_gap": round(max(abs(a - b) for a, b in
                                        zip(elder["drive"][end], newborn["drive"][end])), 5),
            "newborn_drive_learning": round(travelled("drive"), 5),
        })
    return rows


def _history_horizon(close):
    """The first hour from which every hour to the end is close. None if the
    last hour is not: the horizon lies beyond the test."""
    first = None
    for hour in range(len(close) - 1, -1, -1):
        if not close[hour]:
            break
        first = hour
    return first


def _history_text(horizon, hours):
    return f"more than {hours} hours" if horizon is None else f"{horizon} hours"


def _history_twice(control, variant, variant_hours):
    """Is the variant's horizon at least twice the control's? True, False, or
    None when the test is too short to say."""
    if control is None:
        return None                     # the control's own horizon lies beyond its test
    if variant is None:
        return True if variant_hours >= 2 * control else None
    return variant >= 2 * control


def _history_compare(current, baseline):
    """Control against variant for the history gate. The variant passes only if
    its expression horizon is at least twice the control's, its newborn still
    learns, fewer of its links are railed, and its links can still weaken.
    Returns True only if every check that is judged holds.

    The horizon is judged on recorded senses only. On the synthetic day the
    control itself never converges (links the bursts upbringing prunes do not
    regrow in the elder), so there is no horizon there to double. Learning is
    judged on the weights the drive uses: with two-speed links those are what
    the field acts on, and the fast weights alone overstate it."""
    base, cur = baseline.get("history"), current["history"]
    if not base:
        raise SystemExit("--compare with --history needs a file saved by --history --json")
    replay = str(current.get("source", "")).startswith("replay:")
    print(f"\ncompare vs {baseline.get('source')} "
          f"(v{baseline.get('field_version')}, seed {baseline.get('seed')}, "
          f"overrides {baseline.get('overrides')}):")
    print("\n  hour | expression gap      | link gap            | railed links")
    for a, b in zip(base["rows"], cur["rows"]):
        print(f"  {a['hour']:4d} | {a['expression_gap']:.4f} -> {b['expression_gap']:.4f}"
              f"    | {a['link_gap']:.4f} -> {b['link_gap']:.4f}"
              f"    | {a['railed_links']:2d} -> {b['railed_links']:2d}")

    def mean_railed(h):
        return statistics.mean(r["railed_links"] for r in h["rows"])

    def drive_learning(h):
        return sum(r.get("newborn_drive_learning", r["newborn_learning"]) for r in h["rows"][:3])

    fast_share = cur["newborn_learning_3h"] / base["newborn_learning_3h"] if base["newborn_learning_3h"] else 0.0
    print(f"\n  {'link horizon':32s} {_history_text(base['link_horizon'], base['hours'])} -> "
          f"{_history_text(cur['link_horizon'], cur['hours'])}")
    print(f"  {'mean noise gap':32s} {base['noise_gap_mean']:.4f} -> {cur['noise_gap_mean']:.4f}")
    print(f"  {'railed links, mean over the test':32s} {mean_railed(base):.1f} -> {mean_railed(cur):.1f}")
    print(f"  {'newborn learning, fast weights':32s} {base['newborn_learning_3h']:.3f} -> "
          f"{cur['newborn_learning_3h']:.3f} ({fast_share * 100:.0f}% of the control), first 3 hours")
    if not replay:
        print(f"  {'expression horizon':32s} {_history_text(base['expression_horizon'], base['hours'])} -> "
              f"{_history_text(cur['expression_horizon'], cur['hours'])} "
              f"(not judged here: judged on recorded senses, with --replay)")

    print("\n--- VARIANT AGAINST CONTROL ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        mark = "UNDECIDED" if ok is None else "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}")

    if replay:
        twice = _history_twice(base["expression_horizon"], cur["expression_horizon"], cur["hours"])
        detail = (f"control {_history_text(base['expression_horizon'], base['hours'])}, "
                  f"variant {_history_text(cur['expression_horizon'], cur['hours'])}")
        if twice is None:
            detail += ("; the control's horizon lies beyond its test" if base["expression_horizon"] is None
                       else f"; the test would have to run {2 * base['expression_horizon']} hours to say")
        check("the expression horizon is at least twice the control's", twice, detail)
    share = drive_learning(cur) / drive_learning(base) if drive_learning(base) else 0.0
    check("the newborn still learns",
          share >= 0.7,
          f"the weights its drive uses move {drive_learning(cur):.3f} in the first 3 hours against "
          f"{drive_learning(base):.3f}, {share * 100:.0f}% of the control")
    check("fewer railed links",
          cur["railed_links"] < base["railed_links"],
          f"{cur['railed_links']} at the end against {base['railed_links']}")
    still = cur.get("still_room")
    if still:
        check("its links can still weaken", still["weakens"], _history_still_text(still))

    ok = all(c is True for c in checks)
    undecided = sum(1 for c in checks if c is None)
    print(f"\n  {'VARIANT PASS' if ok else 'VARIANT NOT PASSED'} "
          f"({sum(1 for c in checks if c is True)}/{len(checks)} checks hold"
          + (f", {undecided} undecided" if undecided else "") + ")")
    return ok


def history_probe(args):
    """The history gate: how long a newborn field needs, on the same input, to
    become indistinguishable from one that has already lived. It measures and
    changes nothing. The checks test the measurement, not the Creature."""
    apply_overrides(args.set)
    per = HISTORY_TICKS_PER_HOUR
    hours = args.history_hours
    print(f"field {cf.FIELD_VERSION} | HOW FAR BACK THE MEMORY REACHES | seed={args.seed}")

    with tempfile.TemporaryDirectory() as folder:
        if args.replay:
            if not args.state:
                raise SystemExit("--history with --replay needs --state: the elder is the saved field")
            test = _read_replay(args.replay)[:hours * per]
            hours = len(test) // per
            test = test[:hours * per]
            state = args.state
            source = f"replay:{Path(args.replay).name}"
            print(f"\n  elder: {args.state}"
                  f"\n  test input: the first {hours} hours of {args.replay}")
        else:
            state = str(Path(folder) / "elder.json")
            _history_elder(args.seed, args.raise_hours, state)
            # Its own stream, so the noise life's seed is not also the input's.
            test = list(scenario_inputs("day", hours * per, random.Random(args.seed + 2)))
            source = "scenario:bursts then day"
            print(f"\n  elder: raised here for {args.raise_hours} hours on the bursts scenario"
                  f"\n  test input: {hours} hours of the day scenario")
        if hours < 3:
            raise SystemExit("--history needs at least 3 hours of test input")

        elder = _history_life(test, args.seed, state)
        newborn = _history_life(test, args.seed)
        noise = _history_life(test, args.seed + 1, state)
        still = _history_still(args.seed, state)

    rows = _history_rows(elder, newborn, noise, hours)
    expression_horizon = _history_horizon(
        [r["expression_gap"] <= 2.0 * max(r["noise_gap"], HISTORY_NOISE_MIN) for r in rows])
    link_horizon = _history_horizon([r["link_gap"] <= HISTORY_LINK_CLOSE for r in rows])

    print("\n  hour | expression gap | noise gap | link gap | newborn learning | railed links")
    for r in rows:
        print(f"  {r['hour']:4d} | {r['expression_gap']:14.4f} | {r['noise_gap']:9.4f} | "
              f"{r['link_gap']:8.4f} | {r['newborn_learning']:16.3f} | {r['railed_links']:12d}")

    print("\n  ring weights at the end, by link (" + " ".join(f"{i}-{j}" for i, j in elder["keys"]) + "):")
    for name, life in (("elder", elder), ("newborn", newborn)):
        print(f"    {name:8s} " + " ".join(f"{w:.2f}" for w in life["weights"][-1]))

    print(f"\n  expression horizon: {_history_text(expression_horizon, hours)}")
    print(f"  link horizon:       {_history_text(link_horizon, hours)}")
    print(f"  in a still room:    {_history_still_text(still)}")

    noise_mean = statistics.mean(r["noise_gap"] for r in rows)
    learned = sum(r["newborn_learning"] for r in rows[:3])

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("history shows at all",
          rows[0]["expression_gap"] >= 5.0 * rows[0]["noise_gap"],
          f"in hour 0 the expression gap is {rows[0]['expression_gap']:.4f}, "
          f"the noise gap {rows[0]['noise_gap']:.4f}")
    check("the noise floor is small",
          noise_mean < 0.01,
          f"mean noise gap {noise_mean:.4f}")
    check("the newborn learns",
          learned >= 0.2,
          f"its ring weights move {learned:.3f} in total over the first 3 hours")
    check("the horizons are reported",
          len(rows) == hours,
          f"expression {_history_text(expression_horizon, hours)}, "
          f"links {_history_text(link_horizon, hours)}, from {len(rows)} hourly rows")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")

    result = {
        "field_version": cf.FIELD_VERSION,
        "source": source,
        "seed": args.seed,
        "overrides": args.set or [],
        "history": {
            "hours": hours,
            "expression_horizon": expression_horizon,   # None: more than `hours`
            "link_horizon": link_horizon,
            "noise_gap_mean": round(noise_mean, 5),
            "newborn_learning_3h": round(learned, 5),
            "newborn_drive_learning_3h": round(sum(r["newborn_drive_learning"] for r in rows[:3]), 5),
            "railed_links": rows[-1]["railed_links"],
            "still_room": still,
            "rows": rows,
            "weights": {
                name: {f"{i}-{j}": round(w, 5) for (i, j), w in zip(life["keys"], life["weights"][-1])}
                for name, life in (("elder", elder), ("newborn", newborn))
            },
        },
    }
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1))
        print(f"\nsaved: {args.json}")
    if args.compare:
        ok = _history_compare(result, json.loads(Path(args.compare).read_text())) and ok
    return ok


# ---------------------------------------------------------------------------
# The newborn twin (after v06.9)
# ---------------------------------------------------------------------------
#
# The collector can run a second, newborn field beside the real one: the same
# senses, no loop, nothing to the body (mind/twin_v06.py). It shows live what the
# history gate measures offline. Its one rule is that it must not change the
# Creature. The field draws from the shared random stream, so a second field
# stepped beside the first changes it unless it keeps a stream of its own.
#
# Control (the field alone) against variant (the twin beside it), same seed, same
# input, here and then in the collector's own loop on a scripted body.

TWIN_RAISE_HOURS = 6       # the elder's upbringing, on the bursts scenario
TWIN_TEST_HOURS = 2        # then this much of the day scenario
TWIN_LOG_EVERY = 60        # the collector's TWIN_LOG_EVERY_TICKS
TWIN_SCRIPTED_SECONDS = 1800


def _twin_run(inputs, seed, state_path, beside=None):
    """The real field's life on the test input, as a digest of each tick's state
    and signal. `beside` is None (alone), "twin" (the collector's Twin beside it)
    or "shared" (a second field on the same random stream, the mistake the twin
    avoids). With the twin, also its log rows, one a minute."""
    random.seed(seed)
    field = cf.build_field()
    if cf.load_field(field, state_path) is None:
        raise SystemExit(f"could not load a field state from {state_path}")
    decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "fixed"})
    twin = Twin(seed=seed) if beside == "twin" else None
    other = cf.build_field() if beside == "shared" else None
    born = sorted(twin.field.weights.values()) if twin is not None else []
    digests, rows = [], []
    for t, values in enumerate(inputs, 1):
        state = field.step(values)
        signal = decoder.read(state)
        seen = (state["cells"], state["connections"], state["metabolism"],
                state["emitter_activations"], state["events"], signal)
        digests.append(hashlib.blake2b(repr(seen).encode(), digest_size=8).digest())
        if twin is not None:
            twin.step(values, state)
            if t % TWIN_LOG_EVERY == 0:
                rows.append(twin.log_row())
        if other is not None:
            other.step(values)
    return {"digests": digests, "rows": rows, "twin": twin, "born": born}


def twin_probe(args):
    """The twin gate: the Creature is the same with the newborn twin beside it,
    in the field and in the collector's own loop, and the twin measures what the
    history gate measures."""
    apply_overrides(args.set)
    per = HISTORY_TICKS_PER_HOUR
    ticks = TWIN_TEST_HOURS * per
    print(f"field {cf.FIELD_VERSION} | THE NEWBORN TWIN | seed={args.seed}")
    print(f"\n  elder: raised here for {TWIN_RAISE_HOURS} hours on the bursts scenario"
          f"\n  test input: {TWIN_TEST_HOURS} hours of the day scenario")

    with tempfile.TemporaryDirectory() as folder:
        state = str(Path(folder) / "elder.json")
        _history_elder(args.seed, TWIN_RAISE_HOURS, state)
        test = list(scenario_inputs("day", ticks, random.Random(args.seed + 2)))

        alone = _twin_run(test, args.seed, state)
        beside = _twin_run(test, args.seed, state, "twin")
        shared = _twin_run(test, args.seed, state, "shared")
        # The history gate's two lives on the same input, to set the twin's
        # figures against.
        elder = _history_life(test, args.seed, state)
        newborn = _history_life(test, args.seed)

        # A restart: the twin's file is a field state like any other.
        twin = beside["twin"]
        saved = str(Path(folder) / "twin.json")
        twin.save(saved)
        woken = Twin()
        woke = woken.load(saved)
        drift = max(abs(woken.field.weights[k] - w) for k, w in twin.field.weights.items())
        fresh = Twin()
        found = fresh.load(str(Path(folder) / "no_such_twin.json"))

    def differing(a, b):
        return [t for t, (x, y) in enumerate(zip(a["digests"], b["digests"])) if x != y]

    changed = differing(alone, beside)
    changed_shared = differing(alone, shared)

    # The same figures two ways: the twin's log rows, and the history gate's lives.
    samples = per // HISTORY_WEIGHT_EVERY
    rows_per_hour = per // TWIN_LOG_EVERY
    worst = 0.0
    print("\n  hour | arousal gap      | balance gap      | link gap         (twin log / history gate)")
    for hour in range(TWIN_TEST_HOURS):
        lo, hi = hour * per, (hour + 1) * per
        rows = beside["rows"][hour * rows_per_hour:(hour + 1) * rows_per_hour]
        end = (hour + 1) * samples
        pairs = (
            (statistics.mean(r["gap_a"] for r in rows), _history_gap(elder, newborn, "A", lo, hi)),
            (statistics.mean(r["gap_b"] for r in rows), _history_gap(elder, newborn, "B", lo, hi)),
            (rows[-1]["gap_link"],
             max(abs(a - b) for a, b in zip(elder["weights"][end], newborn["weights"][end]))),
        )
        worst = max(worst, max(abs(a - b) for a, b in pairs))
        print(f"  {hour:4d} | " + " | ".join(f"{a:.4f} / {b:.4f}" for a, b in pairs))

    # The collector's own loop, twin off and on, on a scripted body and clock.
    scripted = subprocess.run(
        [sys.executable, str(PROJECT_PYTHON_ROOT / "tools" / "scripted_body.py"), "--twin-check",
         "--seed", str(args.seed), "--seconds", str(TWIN_SCRIPTED_SECONDS)],
        capture_output=True, text=True)
    scripted_lines = [line.strip() for line in scripted.stdout.splitlines() if line.strip()]
    if scripted.returncode != 0:
        print("\n" + scripted.stdout + scripted.stderr)

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("the Creature is the same with the twin beside it",
          not changed,
          f"{ticks - len(changed)} of {ticks} ticks identical: every cell, link and pixel"
          + (f"; first difference at tick {changed[0]}" if changed else ""))
    check("the check can tell: a second field on the shared stream changes it",
          len(changed_shared) > 0,
          f"{len(changed_shared)} of {ticks} ticks differ"
          + (f", from tick {changed_shared[0]}" if changed_shared else ""))
    check("the collector does the same with the twin off and on",
          scripted.returncode == 0,
          f"{TWIN_SCRIPTED_SECONDS} seconds on a scripted body: "
          + (scripted_lines[-1] if scripted_lines else "no output"))
    check("the twin is born new and ages a tick a tick",
          beside["born"] == [INITIAL_WEIGHT] * len(beside["born"]) and twin.age == ticks,
          f"every link at {INITIAL_WEIGHT:.2f} at birth, {twin.age} ticks old after {ticks}")
    check("it measures what the history gate measures",
          worst <= 1e-4,
          f"its log and the gate's two lives agree to within {worst:.6f}")
    check("it survives a restart, and with no file it is a newborn",
          woke and woken.age == twin.age and drift <= 1e-6 and not found and fresh.age == 0,
          f"loaded at {woken.age} ticks old with links within {drift:.7f}; "
          f"no file: {fresh.age} ticks old")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# The battery sets how much the energy reserve can hold (7 October 2026)
# ---------------------------------------------------------------------------
#
# The body has a fuel gauge since 6 October 2026. Here the cell's voltage sets
# the ceiling of the field's shared energy reserve
# (cell_field_v06.BATTERY_CEILING). The claim is narrow: a draining cell dims
# the expression through the energy gate the decoder already has, and changes
# nothing else. The field senses, learns and sleeps exactly as it would have.
#
# Control (the ceiling whole) against variant (the ceiling follows the
# voltage), same seed, same room, same scripted cell: full, a hold where the
# reserve is lower but the gate is still open, a hold half-way down the dimming,
# a hold under the floor, charged back up, full again. The gauge is noisy, as
# the real one is under the body's load.

BATTERY_SCRIPT = [            # (ticks, volts at the start, volts at the end)
    (3600, 4.00, 4.00),       # full
    (900, 4.00, 3.70),
    (1800, 3.70, 3.70),       # high: the reserve is lower, the gate still open
    (900, 3.70, 3.52),
    (1800, 3.52, 3.52),       # mid
    (900, 3.52, 3.35),
    (3600, 3.35, 3.35),       # low, under the floor
    (1800, 3.35, 4.00),       # on the charger
    (3600, 4.00, 4.00),       # full again
]
BATTERY_HOLDS = {"full": 0, "high": 2, "mid": 4, "low": 6, "again": 8}
BATTERY_WINDOW = 1200         # each hold is judged on its last ticks
BATTERY_NOISE = 0.02          # volts, each reading, either way
BATTERY_GAP = (9000, 9060)    # a minute with no reading, in the mid hold


def _battery_volts(seed):
    rng = random.Random(seed + 77)
    for ticks, a, b in BATTERY_SCRIPT:
        for t in range(ticks):
            yield a + (b - a) * t / ticks + rng.uniform(-BATTERY_NOISE, BATTERY_NOISE)


def _battery_windows():
    out, at = {}, 0
    ends = []
    for ticks, _, _ in BATTERY_SCRIPT:
        at += ticks
        ends.append(at)
    for name, i in BATTERY_HOLDS.items():
        out[name] = (ends[i] - BATTERY_WINDOW, ends[i])
    return out


def _battery_run(seed, scenario, ceiling, read=True):
    """One life through the voltage script. The field is read by two decoders,
    the relative model the Pi runs and the fixed one."""
    was = cf.BATTERY_CEILING
    cf.BATTERY_CEILING = ceiling
    try:
        random.seed(seed)
        field = cf.build_field()
        decoders = {m: ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": m})
                    for m in ("relative", "fixed")}
        ticks = sum(n for n, _, _ in BATTERY_SCRIPT)
        ends = set()
        at = 0
        for n, _, _ in BATTERY_SCRIPT:
            at += n
            ends.add(at)
        out = {"A": {m: [] for m in decoders}, "light": {m: [] for m in decoders},
               "tones": {m: [] for m in decoders}, "vol": {m: [] for m in decoders},
               "reserve": [], "ceiling": [], "events": [], "sleeps": [], "weights": [],
               "digest": []}
        last = {m: -FORWARD_VOICE_GAP for m in decoders}
        asleep = False
        inputs = scenario_inputs(scenario, ticks, random.Random(seed + 1))
        for t, (values, volts) in enumerate(zip(inputs, _battery_volts(seed)), 1):
            if not read or BATTERY_GAP[0] <= t < BATTERY_GAP[1]:
                volts = None
            state = field.step(values, battery_v=volts)
            speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
            for m, decoder in decoders.items():
                signal = decoder.read(state)
                out["A"][m].append(signal["A"])
                out["light"][m].append(sum(sum(px) for px in signal["pixels"])
                                       / (255.0 * 4 * len(signal["pixels"])))
                tone = voice_params_from_signal(signal, speaker)
                if tone and t - last[m] >= FORWARD_VOICE_GAP:
                    out["tones"][m].append(t)
                    out["vol"][m].append(tone["vol"])
                    last[m] = t
            out["reserve"].append(field.energy_reserve)
            out["ceiling"].append(field.energy_ceiling_share)
            out["events"].append(len(field.last_events))
            now_asleep = field.sleep_mode == "sleep"
            if now_asleep and not asleep:
                out["sleeps"].append(t)
            asleep = now_asleep
            # Everything of the field but the reserve's own level.
            seen = (state["cells"], state["connections"], state["emitter_activations"],
                    state["events"])
            out["digest"].append(hashlib.blake2b(repr(seen).encode(), digest_size=8).digest())
            if t in ends:
                out["weights"].append(dict(field.weights))
        return out
    finally:
        cf.BATTERY_CEILING = was


def battery_probe(args):
    """The battery gate: with the cell's voltage setting the reserve's ceiling,
    the expression dims as the cell drains and comes back as it charges, never
    goes dark, holds steady under a noisy gauge, and the field itself is the
    same field."""
    apply_overrides(args.set)
    curve = ", ".join(f"{v:.2f} V {s:.2f}" for v, s in cf.BATTERY_CEILING_CURVE)
    print(f"field {cf.FIELD_VERSION} | THE BATTERY SETS THE RESERVE'S CEILING | "
          f"seed={args.seed}, {args.scenario} scenario, curve {curve}")
    control = _battery_run(args.seed, args.scenario, False)
    variant = _battery_run(args.seed, args.scenario, True)
    unread = _battery_run(args.seed, args.scenario, True, read=False)
    windows = _battery_windows()

    def mean(series, name):
        a, b = windows[name]
        return statistics.mean(series[a:b])

    def share(key, model, name):
        base = mean(control[key][model], name)
        return mean(variant[key][model], name) / base if base > 1e-9 else 1.0

    def same(name):
        a, b = windows[name]
        return all(control[k][m][a:b] == variant[k][m][a:b]
                   for k in ("A", "light") for m in ("relative", "fixed"))

    def tones(run_, model, name):
        a, b = windows[name]
        picked = [(t, v) for t, v in zip(run_["tones"][model], run_["vol"][model]) if a < t <= b]
        vol = statistics.mean(v for _, v in picked) if picked else 0.0
        return len(picked), vol

    for model in ("relative", "fixed"):
        print(f"\n  {model} expression model" + (" (what the Pi runs)" if model == "relative" else ""))
        print("    hold    volts  reserve   arousal            strip light        tones (volume)")
        for name, i in BATTERY_HOLDS.items():
            volts = BATTERY_SCRIPT[i][1]
            cn, cv = tones(control, model, name)
            vn, vv = tones(variant, model, name)
            print(f"    {name:6s} {volts:5.2f}  {mean(variant['reserve'], name):6.2f}   "
                  f"{mean(control['A'][model], name):.3f} -> {mean(variant['A'][model], name):.3f}"
                  f" ({share('A', model, name) * 100:3.0f}%)  "
                  f"{mean(control['light'][model], name):.3f} -> {mean(variant['light'][model], name):.3f}"
                  f" ({share('light', model, name) * 100:3.0f}%)  "
                  f"{cn} ({cv:.2f}) -> {vn} ({vv:.2f})")

    full_end = BATTERY_SCRIPT[0][0]
    same_full = all(control[k][m][:full_end] == variant[k][m][:full_end]
                    for k in ("A", "light") for m in ("relative", "fixed"))
    link_gap = max(abs(a[key] - b[key])
                   for a, b in zip(control["weights"], variant["weights"]) for key in a)
    same_field = control["digest"] == variant["digest"]
    low_reserve = min(variant["reserve"])
    unread_same = (unread["digest"] == control["digest"] and unread["reserve"] == control["reserve"]
                   and unread["A"] == control["A"] and unread["light"] == control["light"])
    a, b = windows["mid"]
    steady = max(variant["ceiling"][a:b]) - min(variant["ceiling"][a:b])
    gap = variant["ceiling"][BATTERY_GAP[0] - 1:BATTERY_GAP[1] + 1]
    gap_move = max(gap) - min(gap)

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("at high voltage nothing changes", same_full,
          f"the first {full_end} ticks at {BATTERY_SCRIPT[0][1]:.2f} V, arousal and strip, "
          f"both models: {'identical' if same_full else 'different'}")
    check("down to the knee the reserve falls and the expression does not",
          same("high") and mean(variant["reserve"], "high") < mean(control["reserve"], "high") - 1.0,
          f"at 3.70 V the reserve is {mean(variant['reserve'], 'high'):.2f} against "
          f"{mean(control['reserve'], 'high'):.2f}; arousal and strip "
          f"{'identical' if same('high') else 'different'}")
    mid, low = share("light", "relative", "mid"), share("light", "relative", "low")
    check("the strip dims as the cell drains", low <= 0.7 and low < mid < 1.0,
          f"strip light {mid * 100:.0f}% of the control's at 3.52 V, {low * 100:.0f}% at 3.35 V")
    low_a = share("A", "relative", "low")
    check("it does not go dark", low_a >= 0.2 and mean(variant["light"]["relative"], "low") > 0.0,
          f"arousal {low_a * 100:.0f}% of the control's at the floor")
    check("steady under a noisy gauge", steady <= 0.02 and gap_move <= 0.02,
          f"readings {BATTERY_NOISE * 1000:.0f} mV either way: the ceiling moves by "
          f"{steady:.3f} of the maximum over the 3.52 V hold, and by {gap_move:.3f} "
          f"across a minute with no reading")
    check("no sleep from a low reserve",
          variant["sleeps"] == control["sleeps"] and low_reserve > cf.LOW_RESERVE_SLEEP_THRESHOLD,
          f"{len(variant['sleeps'])} sleeps against {len(control['sleeps'])}, at the same ticks: "
          f"{variant['sleeps'] == control['sleeps']}; the reserve's lowest {low_reserve:.2f} "
          f"(sleep at {cf.LOW_RESERVE_SLEEP_THRESHOLD})")
    check("the field is the same field", same_field and link_gap == 0.0,
          f"cells, links, emitters and events on every tick: "
          f"{'identical' if same_field else 'different'}; largest link gap {link_gap:.6f}")
    again_a, again_l = share("A", "relative", "again"), share("light", "relative", "again")
    check("it comes back on the charger", abs(again_a - 1.0) <= 0.05 and abs(again_l - 1.0) <= 0.05,
          f"arousal {again_a * 100:.0f}% and strip light {again_l * 100:.0f}% of the control's "
          f"once full again")
    check("no reading, no change", unread_same,
          f"the ceiling on but no voltage given: {'identical to' if unread_same else 'different from'} "
          f"the control")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# Where the strip's colour comes from (7 October 2026)
# ---------------------------------------------------------------------------
#
# The strip read as bright white on the body. The W channel was not the cause:
# the palette is a straight RGB line between blue and orange, whose middle is
# grey-white, and the field sits near the middle. The "inner" colour model
# gives the strip one hue that the field's reservoir pushes around the colour
# wheel, at about a third of the light.
#
# Control (the blend) against variant (inner), the same field read by both.

COLOUR_TICKS = 7200
COLOUR_SETTLE = 600
COLOUR_LIT = 12            # a pixel under this is dark and has no colour to judge
COLOUR_WHITE_SAT = 0.3     # saturation under this reads as white


def _colour_run(seed, scenario, ticks, battery_v=None):
    """One life read by both colour models. Per tick: the signal without its
    pixels, the frame, the frame's light."""
    was = cf.BATTERY_CEILING
    cf.BATTERY_CEILING = battery_v is not None
    try:
        random.seed(seed)
        field = cf.build_field()
        decoders = {m: ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "relative", "COLOUR_MODEL": m})
                    for m in ("blend", "inner")}
        out = {m: {"signal": [], "frames": [], "light": [], "hue": []} for m in decoders}
        for values in scenario_inputs(scenario, ticks, random.Random(seed + 1)):
            state = field.step(values, battery_v=battery_v)
            for m, decoder in decoders.items():
                signal = decoder.read(state)
                o = out[m]
                o["signal"].append((signal["A"], signal["B"], signal["T"], signal["event"],
                                    repr(signal.get("voice"))))
                o["frames"].append(signal["pixels"])
                o["light"].append(sum(sum(px) for px in signal["pixels"]) / len(signal["pixels"]))
                o["hue"].append(signal.get("hue"))
        return out
    finally:
        cf.BATTERY_CEILING = was


def _colour_stats(frames):
    """Share of lit pixels that read as white, the share of the coloured ones in
    each twelfth of the wheel, and the usual spread of hue along one frame."""
    lit = white = 0
    sectors = [0] * 12
    spreads = []
    for frame in frames:
        hues = []
        for r, g, b, w in frame:
            if max(r, g, b) + w < COLOUR_LIT:
                continue
            lit += 1
            h, sat, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if sat < COLOUR_WHITE_SAT or w > max(r, g, b):
                white += 1
            else:
                sectors[int(h * 12) % 12] += 1
                hues.append(h * 360.0)
        if len(hues) >= 2:
            # the widest gap between neighbouring hues, taken off the full circle
            hues.sort()
            gaps = [b - a for a, b in zip(hues, hues[1:])] + [hues[0] + 360.0 - hues[-1]]
            spreads.append(360.0 - max(gaps))
    coloured = max(1, sum(sectors))
    return {"white": white / max(1, lit),
            "sectors": [c / coloured for c in sectors],
            "spread": statistics.median(spreads) if spreads else 0.0}


def _corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0.0 or vb <= 0.0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def colour_probe(args):
    """The colour gate: under the inner model the strip is hardly ever white,
    uses the whole colour wheel, never flickers, is about a third as bright,
    still follows the field's activity, draws nothing by chance, and leaves the
    rest of the expression alone."""
    apply_overrides(args.set)
    print(f"field {cf.FIELD_VERSION} | WHERE THE STRIP'S COLOUR COMES FROM | "
          f"seed={args.seed}, {args.scenario} scenario, {COLOUR_TICKS} ticks")
    run_ = _colour_run(args.seed, args.scenario, COLOUR_TICKS)
    again = _colour_run(args.seed, args.scenario, COLOUR_TICKS)
    other = _colour_run(args.seed + 100, args.scenario, COLOUR_TICKS)
    low = _colour_run(args.seed, args.scenario, 1800, battery_v=3.35)
    blend, inner = run_["blend"], run_["inner"]
    skip = COLOUR_SETTLE
    stats = {m: _colour_stats(run_[m]["frames"][skip:]) for m in run_}

    for m, name in (("blend", "blend (control)"), ("inner", "inner (variant)")):
        st = stats[m]
        used = sum(1 for share in st["sectors"] if share >= 0.02)
        print(f"\n  {name}")
        print(f"    light per pixel {statistics.mean(run_[m]['light'][skip:]):6.1f} | white "
              f"{st['white'] * 100:3.0f}% of lit pixels | {used}/12 of the wheel | "
              f"hue spread along the strip {st['spread']:.0f} degrees")
        print("    share of each twelfth: " + " ".join(f"{share * 100:2.0f}" for share in st["sectors"]))

    hue = inner["hue"]
    steps = [min(abs(a - b), 360.0 - abs(a - b)) for a, b in zip(hue[skip:], hue[skip + 1:])]
    hour = [len({int(h // 30) % 12 for h in hue[i:i + 3600]}) for i in range(0, len(hue) - 3599, 3600)]
    ratio = statistics.mean(inner["light"][skip:]) / statistics.mean(blend["light"][skip:])
    follow = _corr(inner["light"][skip:], blend["light"][skip:])
    used = sum(1 for share in stats["inner"]["sectors"] if share >= 0.02)
    low_ratio = (statistics.mean(low["inner"]["light"][skip:])
                 / statistics.mean(inner["light"][skip:1800]))
    rate_max = ExpressionDecoderV06().knobs["HUE_RATE_MAX"]

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    check("the rest of the expression is untouched", blend["signal"] == inner["signal"],
          "arousal, balance, tempo, events and the voice's decisions on every tick: "
          + ("identical" if blend["signal"] == inner["signal"] else "different"))
    check("hardly ever white", stats["inner"]["white"] <= 0.02,
          f"{stats['inner']['white'] * 100:.0f}% of lit pixels read as white, against "
          f"{stats['blend']['white'] * 100:.0f}%")
    check("the whole colour wheel", used >= 10 and min(hour) >= 6,
          f"{used}/12 of the wheel holds at least 2% of the coloured pixels (the blend: "
          f"{sum(1 for share in stats['blend']['sectors'] if share >= 0.02)}/12); "
          f"the hue visits {min(hour)} to {max(hour)} twelfths in an hour")
    check("no flicker", max(steps) <= rate_max + 1e-6,
          f"the hue turns {statistics.mean(steps):.1f} degrees a tick on average, "
          f"{max(steps):.1f} at most")
    check("about a third as bright", 0.25 <= ratio <= 0.45,
          f"{ratio * 100:.0f}% of the blend's light")
    check("brightness still follows the field", follow >= 0.8,
          f"the two models' light rises and falls together, correlation {follow:.2f}")
    check("the strip is not one flat colour", stats["inner"]["spread"] >= 15.0,
          f"a usual frame spans {stats['inner']['spread']:.0f} degrees of hue from its warm "
          f"end to its cool end")
    check("nothing by chance",
          again["inner"]["frames"] == inner["frames"] and other["inner"]["hue"][-1] != hue[-1],
          f"the same life twice: {'identical' if again['inner']['frames'] == inner['frames'] else 'different'} "
          f"frames; another life ends on hue {other['inner']['hue'][-1]:.0f}, this one on {hue[-1]:.0f}")
    check("a low battery still dims it", low_ratio <= 0.7,
          f"at 3.35 V the strip is sent {low_ratio * 100:.0f}% of its usual light")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# What a tone sounds like (7 October 2026)
# ---------------------------------------------------------------------------
#
# The voice was one plain sine beep, 220 to 440 Hz, the small speaker's weakest
# corner. Josh auditioned shapes, overtones and the range on the real speaker:
# fewer beeps, more swells and soft tones, a mix of overtones, and the top of
# the range only very lightly. The "open" palette is that.
#
# Control (beep) against variant (open), the same field read by both, under the
# relative voice rule and the inner colour model as on the Pi.

PALETTE_TICKS = 14400


def _palette_run(seed, scenario, ticks):
    random.seed(seed)
    field = cf.build_field()
    decoders = {m: ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "relative", "COLOUR_MODEL": "inner",
                                               "VOICE_MODEL": "relative", "VOICE_PALETTE": m})
                for m in ("beep", "open")}
    tones = {m: [] for m in decoders}
    last = {m: -FORWARD_VOICE_GAP for m in decoders}
    for t, values in enumerate(scenario_inputs(scenario, ticks, random.Random(seed + 1))):
        state = field.step(values)
        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        for m, decoder in decoders.items():
            tone = voice_params_from_signal(decoder.read(state), speaker)
            if tone and t - last[m] >= FORWARD_VOICE_GAP:
                tones[m].append((t, tone))
                last[m] = t
    return tones


def _palette_learn(tones, seed, use_level=True):
    """A sound model hearing these tones from the simulated speaker. Returns how
    much of its own voice it explains at the end."""
    room = random.Random(seed + 5)
    model = fm.SoundModel()
    for _, tone in tones:
        told = dict(tone) if use_level else {k: v for k, v in tone.items() if k != "level"}
        model.step(told, None, None, heard=_heard(tone, room))
    return model.explained()


def palette_probe(args):
    """The palette gate: under the open palette the voice speaks at the same
    moments, never as a beep, on a small scale where the speaker speaks, with
    the high notes rare and light and the overtones a mix, and the sound model
    still learns to predict its own voice."""
    apply_overrides(args.set)
    print(f"field {cf.FIELD_VERSION} | WHAT A TONE SOUNDS LIKE | seed={args.seed}, "
          f"{args.scenario} scenario, {PALETTE_TICKS} ticks")
    tones = _palette_run(args.seed, args.scenario, PALETTE_TICKS)
    knobs = ExpressionDecoderV06().knobs
    main_notes, light_notes = set(knobs["OPEN_NOTES"]), set(knobs["OPEN_LIGHT_NOTES"])
    main_notes = {round(n, 1) for n in main_notes}
    light_notes = {round(n, 1) for n in light_notes}

    for name in ("beep", "open"):
        ts = [tone for _, tone in tones[name]]
        freqs = sorted({tone["freq"] for tone in ts})
        print(f"\n  {name} palette" + (" (control)" if name == "beep" else " (variant)"))
        print(f"    {len(ts)} tones, {len(freqs)} different pitches, "
              f"{freqs[0]:.0f} to {freqs[-1]:.0f} Hz" if ts else "    no tones")
        if name == "open" and ts:
            counts = {f: sum(1 for tone in ts if tone["freq"] == f) for f in freqs}
            print("    notes: " + ", ".join(f"{f:.0f} Hz x{n}" for f, n in counts.items()))
            print(f"    rise {min(t['attack'] for t in ts)} to {max(t['attack'] for t in ts)} ms, "
                  f"fall {min(t['release'] for t in ts)} to {max(t['release'] for t in ts)} ms, "
                  f"length {min(t['ms'] for t in ts)} to {max(t['ms'] for t in ts)} ms")

    beeps, opens = tones["beep"], [tone for _, tone in tones["open"]]
    light = [t for t in opens if t["freq"] in light_notes]
    main = [t for t in opens if t["freq"] in main_notes]
    swells = sum(1 for t in main if t["attack"] >= 300)
    h2 = [t["h2"] for t in main]
    h3 = [t["h3"] for t in main]
    shapes = {(t["freq"], t["attack"] // 60, round(t["h2"], 1), round(t["h3"], 1)) for t in opens}

    was = (fm.VOICE_PALETTE,)
    fm.set_voice_palette("beep")
    learn_beep = _palette_learn(beeps, args.seed)
    fm.set_voice_palette("open")
    learn_open = _palette_learn(tones["open"], args.seed)
    learn_blind = _palette_learn(tones["open"], args.seed, use_level=False)
    probe = Curiosity(seed=args.seed + 7)
    probes = []
    model = fm.SoundModel()
    for _ in range(40000):
        out = probe.step(10 ** 6, model)
        if out["voice"]:
            probes.append(out["voice"])
            probe.note_voice()
            model.pitch_n[fm.pitch_bin(out["voice"]["freq"])] += 1
    fm.set_voice_palette(was[0])

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    same_when = [t for t, _ in beeps] == [t for t, _ in tones["open"]]
    check("it speaks at the same moments", same_when and len(opens) >= 10,
          f"{len(opens)} tones against {len(beeps)}, at the same ticks: {same_when}")
    check("no beeps", all(t["attack"] >= 100 and t["release"] >= 100 for t in opens),
          f"every tone rises over at least {min(t['attack'] for t in opens)} ms and falls over at "
          f"least {min(t['release'] for t in opens)} ms; {swells} of {len(main)} are long swells")
    check("where the speaker speaks", all(t["freq"] in main_notes | light_notes for t in opens)
          and min(t["freq"] for t in opens) >= 450.0,
          f"every pitch is one of {len(main_notes) + len(light_notes)} notes, none under 450 Hz "
          f"(the beep: all at or under {max(tone['freq'] for _, tone in beeps):.0f} Hz)")
    check("it uses its scale", len({t["freq"] for t in main}) >= 4,
          f"{len({t['freq'] for t in main})} of the {len(main_notes)} main notes heard")
    check("the high notes are rare and light",
          len(light) <= 0.15 * len(opens) and all(t["vol"] <= 0.4 and t["h2"] == 0.0 for t in light)
          and all(t["vol"] <= 0.7 for t in main),
          f"{len(light)} of {len(opens)} tones are high, at volume "
          f"{max([t['vol'] for t in light], default=0.0):.2f} with no overtones; the rest at "
          f"{max(t['vol'] for t in main):.2f}")
    check("a mix of overtones", max(h2) - min(h2) >= 0.25 and max(h3) - min(h3) >= 0.15,
          f"the second from {min(h2):.2f} to {max(h2):.2f}, the third from {min(h3):.2f} to {max(h3):.2f}")
    check("more than a handful of sounds", len(shapes) >= 3 * len({tone["freq"] for _, tone in beeps}) or len(shapes) >= 20,
          f"{len(shapes)} tones that differ in note, rise or overtones")
    check("it still learns its own voice", learn_open >= 0.5 and learn_open >= learn_blind,
          f"the sound model explains {learn_open * 100:.0f}% of what it hears of itself "
          f"({learn_beep * 100:.0f}% under the beep; {learn_blind * 100:.0f}% if it is not told a "
          f"tone's shape)")
    check("curiosity's probes are soft and keep to the main range",
          bool(probes) and all(p.get("attack", 0) >= 100 and p["freq"] <= 800.0 for p in probes),
          f"{len(probes)} probe tones, {min(p['freq'] for p in probes):.0f} to "
          f"{max(p['freq'] for p in probes):.0f} Hz, each rising over {probes[0].get('attack', 0)} ms"
          if probes else "no probe tones")

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


# ---------------------------------------------------------------------------
# A calm strip in a dark room (9 October 2026)
# ---------------------------------------------------------------------------
#
# Josh does not want the strip flashy in a dark room, and the strip sits at
# different distances from the light sensor at different times. The rule
# (mind/calm_v06.py) works out the room's own level, with the strip's light
# taken out as far as it has learned what the sensor sees of the strip, and,
# the darker that is, shows each frame dimmer and follows it more slowly.
#
# The first design took the room's level from the lowest reading of the last
# minute alone and failed this gate 7 of 14: with the strip close, its own
# light made a dark room read as lit.
#
# Control (no rule) against variant (the rule), the same seed and the same room:
# a lit hour, a dark and still hour, a dark hour with the day's sound and motion
# in it, then the light back on. The body is the Pi's: the relative expression
# model, the inner colour model, the loop felt, curiosity probing. The light
# sensor sits beside one part of the strip and sees each channel as the loop
# probe of 9 October measured it. It is run at three distances.
#
# The strip is judged as the body shows it: the firmware scales every value by
# its brightness cap (40 of 255), so a value under 7 is dark.

DARK_SEEN = (62.6, 137.8, 36.5, 240.0)   # lux per channel at value 200, the probe of 9 October 2026
DARK_DISTANCES = (
    ("far, as on 3 October", 1.54 / 410.9),
    ("an inch or two, as on 9 October", 1.0),
    ("closer still", 2.0),
)
DARK_HOME = 1                 # the distance the gate is judged at
DARK_PHASES = (("lit", 3600), ("dark and still", 3600), ("dark and busy", 3600), ("lit again", 1800))
DARK_ROOM_LIT = 150.0         # lux, the room on the evening of 9 October with the strip dark
DARK_ROOM_DARK = 0.3
DARK_SETTLE = 300             # ticks into a phase before it is judged
DARK_BORN = (("dark and busy", 900),)   # a start in a dark room
DARK_FIRMWARE_CAP = 40        # the body's STRIP_MAX_BRIGHTNESS


def _dark_script(seed, phases=DARK_PHASES):
    """Per tick: (phase, the room's senses, the room's own lux)."""
    total = sum(n for _, n in phases)
    day = scenario_inputs("day", total, random.Random(seed + 1))
    night = _still_night(total)
    for phase, (name, ticks) in enumerate(phases):
        for _ in range(ticks):
            busy, still = next(day), next(night)
            if name.startswith("lit"):
                # the room's own light keeps the day's rise and fall
                yield phase, busy, DARK_ROOM_LIT + 300.0 * busy["light"]
            elif name == "dark and still":
                yield phase, still, DARK_ROOM_DARK
            else:
                yield phase, dict(busy, light=0.0), DARK_ROOM_DARK


def _dark_shown(pixels):
    """The frame as the body's strip shows it, after the firmware's cap."""
    return [tuple((c * (DARK_FIRMWARE_CAP + 1)) >> 8 for c in px) for px in pixels]


def _dark_seen(pixels, nearness):
    """Lux the strip adds at a sensor beside part of it, from what the strip
    really shows. Nearby pixels count most."""
    total = [0.0] * 4
    weight = 0.0
    full = (200 * (DARK_FIRMWARE_CAP + 1)) >> 8
    for p, px in enumerate(_dark_shown(pixels)):
        wt = math.exp(-((p - EXPRESS_SENSOR_PIXEL) / EXPRESS_SENSOR_WIDTH) ** 2)
        weight += wt
        for ch in range(4):
            total[ch] += wt * px[ch] / full
    return nearness * sum(DARK_SEEN[ch] * total[ch] / weight for ch in range(4))


def _dark_run(seed, nearness, rule, phases=DARK_PHASES):
    """One life through the phases. Returns per-tick tracks."""
    random.seed(seed)
    room = random.Random(seed + 2)
    field = cf.build_field()
    decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "relative", "COLOUR_MODEL": "inner"})
    model = ForwardModel()
    curiosity = Curiosity(seed + 3)
    calm = DarkCalm() if rule else None
    light = RollingNormalizer(EXPRESS_LIGHT_WINDOW, EXPRESS_LIGHT_ALPHA, EXPRESS_LIGHT_MIN_RANGE)

    own = 0.0
    action = None
    last_voice = -FORWARD_VOICE_GAP
    out = {"phase": [], "sent": [], "shown": [], "calm": [], "room": [], "own": [], "events": [],
           "arousal": [], "asleep": [], "probe": [], "tones": 0}
    for t, (phase, values, room_lux) in enumerate(_dark_script(seed, phases)):
        seen = []
        for i in range(10):          # the body samples at about 10 Hz
            lux = max(0.0, room_lux + own + room.gauss(0.0, 0.4))
            seen.append(lux)
            if calm is not None:
                calm.note_lux(lux)
            # as the collector: what the model credits to the strip is taken out
            light.add(max(0.0, lux - model.light.own_lux((action or {}).get("rgbw"))), t + i / 10.0)
        rms_mean = 4000.0 + 800.0 * room.random()
        returned = {"lux": statistics.mean(seen), "rms_mean": rms_mean,
                    "rms_max": rms_mean + 1500.0 * room.random(), "vox": None}
        result = model.step(action, returned) if action is not None else None
        state = field.step(dict(values, light=light.normalized()), loop=(result or {}).get("feel"))
        asleep = state["metabolism"]["mode"] == "sleep"

        probe = curiosity.step(state["metabolism"]["ticks_since_event"], model.sound)
        if asleep:
            probe = None
        signal = decoder.read(state)
        pixels = signal["pixels"]
        if probe and probe["light"]:
            pixels = lift_white(pixels, probe["light"], 200)
        if calm is not None:
            calm.step()
            pixels = calm.frame(pixels)

        speaker = (state.get("emitter_activations") or {}).get("speaker", 0.0)
        voice = voice_params_from_signal(signal, speaker)
        if voice and t - last_voice < FORWARD_VOICE_GAP:
            voice = None
        elif not voice and probe and probe["voice"] and t - last_voice >= FORWARD_VOICE_GAP:
            voice = dict(probe["voice"])
        if voice:
            last_voice = t
            curiosity.note_voice()
            out["tones"] += 1
        # The tone is not heard back here: this gate is about the light.
        action = {"rgbw": frame_rgbw(pixels), "voice": None}
        own = _dark_seen(pixels, nearness)

        out["phase"].append(phase)
        out["sent"].append(pixels)
        out["shown"].append(_dark_shown(pixels))
        out["calm"].append(calm.calm if calm is not None else 0.0)
        out["room"].append(calm.room_lux if calm is not None else None)
        out["own"].append(own)
        out["events"].append(bool(field.last_events))
        out["arousal"].append(float(signal.get("A", 0.0) or 0.0))
        out["asleep"].append(asleep)
        out["probe"].append(probe["light"] if probe else 0.0)
    out["ring"] = ring_metrics(field)
    return out


def _dark_stats(run_, phase, settle=DARK_SETTLE):
    """One phase, judged once it has settled, as the body's strip shows it."""
    ticks = [t for t, ph in enumerate(run_["phase"]) if ph == phase][settle:]
    light = [sum(sum(px) for px in run_["shown"][t]) for t in ticks]
    # The biggest change in any one pixel from one tick to the next, and the
    # change in the whole strip's light.
    pixel_jump = []
    strip_jump = []
    for t in ticks[1:]:
        was, now = run_["shown"][t - 1], run_["shown"][t]
        pixel_jump.append(max(abs(sum(a) - sum(b)) for a, b in zip(was, now)))
        strip_jump.append(abs(sum(sum(px) for px in now) - sum(sum(px) for px in was)))
    lux = [run_["own"][t] for t in ticks]
    lux_jump = [abs(a - b) for a, b in zip(lux, lux[1:])]
    return {
        "light": statistics.mean(light),
        "light_max": max(light),
        "lit_pixels": statistics.mean(sum(1 for px in run_["shown"][t] if sum(px) > 0) for t in ticks),
        "dark_frames": sum(1 for v in light if v == 0) / len(light),
        "pixel_jump": max(pixel_jump),
        "strip_jump": max(strip_jump),
        "strip_jump_usual": statistics.mean(strip_jump),
        "moving": sum(1 for v in strip_jump if v > 0) / len(strip_jump),
        "white": max(px[3] for t in ticks for px in run_["shown"][t]),
        "lux": statistics.mean(lux),
        "lux_jump": max(lux_jump),
        "lux_jump_usual": statistics.mean(lux_jump),
        "events": sum(run_["events"][t] for t in ticks) / len(ticks),
        "arousal": statistics.mean(run_["arousal"][t] for t in ticks),
        "pinned": sum(1 for t in ticks if run_["arousal"][t] > 0.99) / len(ticks),
        "asleep": sum(run_["asleep"][t] for t in ticks) / len(ticks),
        "probes": sum(1 for t in ticks if run_["probe"][t] > 0.05),
        "calm": statistics.mean(run_["calm"][t] for t in ticks),
    }


def dark_probe(args):
    """The dark-room gate: with the rule the strip is dim and slow in a dark
    room, whether the room is still or busy and wherever the strip sits; a lit
    room is left exactly as it was; and the field is not harmed."""
    apply_overrides(args.set)
    seed = args.seed
    starts = [0]
    for _, n in DARK_PHASES:
        starts.append(starts[-1] + n)
    print(f"field {cf.FIELD_VERSION} | A CALM STRIP IN A DARK ROOM | seed={seed} | "
          + ", ".join(f"{name} {n}" for name, n in DARK_PHASES) + " ticks")
    print(f"  rule: dark at {calm_v06.DARK_LUX:g} lux, lit at {calm_v06.LIT_LUX:g}; in the dark "
          f"{calm_v06.DARK_BRIGHTNESS:g} of the brightness, {calm_v06.DARK_FOLLOW:g} of the way "
          f"to each new frame; the strip credited {calm_v06.OWN_MARGIN:g} over what was learned")

    runs = {}
    for d, (name, nearness) in enumerate(DARK_DISTANCES):
        runs[d] = {"control": _dark_run(seed, nearness, False), "variant": _dark_run(seed, nearness, True)}
    again = _dark_run(seed, DARK_DISTANCES[DARK_HOME][1], True)
    # A restart in the dark: nothing of the rule is saved, so it starts not
    # knowing what the sensor sees of the strip.
    born = [_dark_run(seed, nearness, True, DARK_BORN) for _, nearness in DARK_DISTANCES]

    stats = {d: {k: [_dark_stats(run_, ph) for ph in range(len(DARK_PHASES))] for k, run_ in pair.items()}
             for d, pair in runs.items()}
    for d, (name, nearness) in enumerate(DARK_DISTANCES):
        full = nearness * sum(DARK_SEEN)
        print(f"\n  the strip {name} ({full:.1f} lux at the sensor with every channel at 200)")
        print("                              strip light   lit px   biggest jump   usual jump   "
              "moving   own lux   events   arousal   asleep   calm")
        for ph, (phase, _) in enumerate(DARK_PHASES):
            for k, label in (("control", "no rule"), ("variant", "rule")):
                st = stats[d][k][ph]
                print(f"    {phase:15s} {label:8s} {st['light']:8.1f}   {st['lit_pixels']:6.1f}   "
                      f"{st['strip_jump']:8d}       {st['strip_jump_usual']:6.2f}     "
                      f"{st['moving'] * 100:4.0f}%   {st['lux']:7.2f}   {st['events'] * 100:5.1f}%   "
                      f"{st['arousal']:.3f}     {st['asleep'] * 100:4.0f}%   {st['calm']:.2f}")

    home = runs[DARK_HOME]
    control, variant = stats[DARK_HOME]["control"], stats[DARK_HOME]["variant"]
    LIT, STILL, BUSY, BACK = range(4)

    print("\n--- GATE RESULT ---")
    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    same = all(runs[d]["control"]["sent"][:starts[1]] == runs[d]["variant"]["sent"][:starts[1]]
               for d in runs)
    check("a lit room is left alone", same,
          "every frame of the lit hour, at all three distances: "
          + ("identical with and without the rule" if same else "different"))

    def times(ph, key):
        return variant[ph][key] / max(1e-9, control[ph][key])

    for ph, name in ((STILL, "dark and still"), (BUSY, "dark and busy")):
        check(f"{name}: no flashes",
              variant[ph]["strip_jump"] <= 0.2 * control[ph]["strip_jump"]
              and variant[ph]["pixel_jump"] <= min(4, 0.2 * control[ph]["pixel_jump"]),
              f"the biggest change of the whole strip in one tick is {variant[ph]['strip_jump']} "
              f"against {control[ph]['strip_jump']} without the rule; of one pixel "
              f"{variant[ph]['pixel_jump']} against {control[ph]['pixel_jump']}")
        check(f"{name}: slow",
              variant[ph]["strip_jump_usual"] <= 0.25 * control[ph]["strip_jump_usual"],
              f"from tick to tick the strip's light changes by {variant[ph]['strip_jump_usual']:.2f} "
              f"on average against {control[ph]['strip_jump_usual']:.2f}; it changes at all on "
              f"{variant[ph]['moving'] * 100:.0f}% of ticks against {control[ph]['moving'] * 100:.0f}%")
        check(f"{name}: dim",
              variant[ph]["light"] <= 0.5 * control[ph]["light"],
              f"{times(ph, 'light') * 100:.0f}% of the light it shows without the rule "
              f"({variant[ph]['light']:.1f} against {control[ph]['light']:.1f})")
        check(f"{name}: the probe's white does not show",
              variant[ph]["white"] <= 1,
              f"the white channel reaches {variant[ph]['white']} on the strip against "
              f"{control[ph]['white']} without the rule ({control[ph]['probes']} bursts proposed there, "
              f"{variant[ph]['probes']} here)")
    check("dark and busy: it is still there",
          variant[BUSY]["dark_frames"] <= 0.5,
          f"some of the strip is lit on {(1 - variant[BUSY]['dark_frames']) * 100:.0f}% of ticks, "
          f"{variant[BUSY]['lit_pixels']:.1f} pixels on average (still room: "
          f"{(1 - variant[STILL]['dark_frames']) * 100:.0f}%, {variant[STILL]['lit_pixels']:.1f} pixels)")

    def reach(run_, start, level, above):
        for i, c in enumerate(run_["calm"][start:]):
            if (c >= level) if above else (c <= level):
                return i
        return None

    falls = [reach(runs[d]["variant"], starts[1], 0.9, True) for d in runs]
    holds = [min(runs[d]["variant"]["calm"][starts[1] + 120:starts[3]]) for d in runs]
    check("it knows the dark wherever the strip sits",
          all(f is not None and f <= 120 for f in falls) and min(holds) >= 0.9,
          "calm from " + ", ".join(
              f"{'never' if f is None else f} ticks" for f in falls)
          + " after the light goes, and never under "
          + ", ".join(f"{h:.2f}" for h in holds) + " for the rest of the dark (far, near, closer)")
    starts_dark = [reach(run_, 0, 0.9, True) for run_ in born]
    held = [min(run_["calm"][300:]) for run_ in born]
    check("started in the dark, it finds out",
          all(f is not None and f <= 300 for f in starts_dark) and min(held) >= 0.9,
          "calm " + ", ".join(f"{'never' if f is None else f}" for f in starts_dark)
          + " ticks after a start in a dark busy room, and never under "
          + ", ".join(f"{h:.2f}" for h in held) + " from five minutes in")
    wakes = [reach(runs[d]["variant"], starts[3], 0.0, False) for d in runs]
    check("light again, it lets go",
          all(w is not None and w <= 180 for w in wakes)
          and variant[BACK]["light"] >= 0.6 * control[BACK]["light"],
          "calm is gone " + ", ".join(f"{'never' if w is None else w}" for w in wakes)
          + f" ticks after the light returns; the strip then shows "
          f"{times(BACK, 'light') * 100:.0f}% of what it shows without the rule")
    # Judged against the creature whose sensor does not see its strip: for it
    # too a dark still room is a quiet one. That was the Creature's whole life
    # up to October 2026.
    far = stats[0]["control"]
    ring_far = runs[0]["control"]["ring"]
    ring_c, ring_v = home["control"]["ring"], home["variant"]["ring"]
    pinned = max(variant[ph]["pinned"] for ph in range(len(DARK_PHASES)))
    pinned_far = max(far[ph]["pinned"] for ph in range(len(DARK_PHASES)))
    # Full arousal on under a tenth of the ticks is the --curious gate's bar
    # for a creature that sees its own light without being pinned by it.
    check("the field is not harmed",
          pinned < 0.1
          and variant[BUSY]["events"] >= 0.5 * control[BUSY]["events"]
          and variant[BACK]["events"] >= 0.5 * control[BACK]["events"]
          and ring_v["live_links"] >= ring_far["live_links"]
          and ring_v["weight_mean_all"] >= 0.9 * ring_far["weight_mean_all"],
          f"at full arousal on at most {pinned * 100:.1f}% of a phase's ticks ({pinned_far * 100:.1f}% "
          f"with a far strip and no rule); events in the busy dark on {variant[BUSY]['events'] * 100:.1f}% "
          f"of ticks against {control[BUSY]['events'] * 100:.1f}%, after the light returns "
          f"{variant[BACK]['events'] * 100:.1f}% against {control[BACK]['events'] * 100:.1f}%; "
          f"{ring_v['live_links']} live links, mean weight {ring_v['weight_mean_all']:.3f} "
          f"({ring_far['weight_mean_all']:.3f} with a far strip)")
    # Not a check: the price. In a still dark room a strip the sensor sees
    # keeps the creature stirring; a calm strip does not.
    print(f"         the price, in the still dark: events on {variant[STILL]['events'] * 100:.1f}% of ticks "
          f"against {control[STILL]['events'] * 100:.1f}% without the rule, mean arousal "
          f"{variant[STILL]['arousal']:.3f} against {control[STILL]['arousal']:.3f}, asleep "
          f"{variant[STILL]['asleep'] * 100:.0f}% against {control[STILL]['asleep'] * 100:.0f}%, and the "
          f"links end at a mean weight of {ring_v['weight_mean_all']:.3f} against {ring_c['weight_mean_all']:.3f}")
    check("nothing by chance", again["sent"] == home["variant"]["sent"],
          "the same life twice: " + ("identical frames" if again["sent"] == home["variant"]["sent"]
                                     else "different frames"))

    ok = all(checks)
    print(f"\n  {'GATE PASS' if ok else 'GATE FAIL'} ({sum(checks)}/{len(checks)} checks)")
    return ok


def compare(current, baseline):
    print(f"\ncompare vs {baseline.get('source')} "
          f"(v{baseline.get('field_version')}, seed {baseline.get('seed')}, "
          f"overrides {baseline.get('overrides')}):")
    cur_f, base_f = current["final"], baseline["final"]
    for key in sorted(set(cur_f) & set(base_f)):
        if key in ("state_counts", "per_cell_incident"):
            continue
        a, b = base_f[key], cur_f[key]
        if isinstance(a, (int, float)) and a != b:
            print(f"  {key:28s} {a:>10} -> {b:>10}")
    wa, wb = baseline.get("weights", {}), current.get("weights", {})
    shared = set(wa) & set(wb)
    if shared:
        diffs = [abs(wa[k] - wb[k]) for k in shared]
        print(f"  weight map: mean |diff| = {statistics.mean(diffs):.5f}, "
              f"max |diff| = {max(diffs):.5f} over {len(shared)} links")


def main():
    p = argparse.ArgumentParser(description="Offline replay harness for the v06 ring.")
    p.add_argument("--ticks", type=int, default=20000)
    p.add_argument("--night", type=int, default=20000,
                   help="quiet ticks after the day, for --gate")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--scenario", choices=["day", "bursts", "quiet"], default="day")
    p.add_argument("--gate", action="store_true",
                   help="run the step-1 acceptance gate (day then quiet night)")
    p.add_argument("--reservoir", action="store_true",
                   help="run the step-2 reservoir gate (distinguishability, "
                        "echo state property, spectral radius sweep)")
    p.add_argument("--readout", action="store_true",
                   help="run the step-3 readout gate (reservoir readout vs a "
                        "direct outer-ring connection)")
    p.add_argument("--predictive", action="store_true",
                   help="run the step-4 predictive-cell gate (no flattening to "
                        "uniform under steady input, leaky vs predictive)")
    p.add_argument("--exprmem", action="store_true",
                   help="run the expression-memory step-1 gate (passive "
                        "recorder: an autobiography graph forms, two lives are "
                        "distinguishable)")
    p.add_argument("--expr-bins", type=int, default=5,
                   help="expression-memory resolution: bins per signal "
                        "dimension (default 5)")
    p.add_argument("--expr-decay", type=float, default=0.999,
                   help="expression-memory edge decay per tick "
                        "(1.0 = no decay; default 0.999)")
    p.add_argument("--exprbias", action="store_true",
                   help="run the expression-memory step-2 bias sweep (field vs "
                        "habit mixing weight; watches for groove collapse)")
    p.add_argument("--exprnov", action="store_true",
                   help="run the expression-memory step-3 novelty map "
                        "(habit vs adaptive novelty; finds the temperament band)")
    p.add_argument("--darkroom", action="store_true",
                   help="run the dark-room loop probe (open-loop control vs "
                        "closed-loop-with-curiosity in an empty room)")
    p.add_argument("--forward", action="store_true",
                   help="run the forward-model gate (it learns its own light "
                        "and voice from what it emits and what returns)")
    p.add_argument("--feel", action="store_true",
                   help="run the loop-is-felt gate (the forward model's result "
                        "reaches the two loop cells; control vs variant)")
    p.add_argument("--curious", action="store_true",
                   help="run the curiosity gate (bored in a still dark room, it "
                        "probes and learns its own voice)")
    p.add_argument("--voice", action="store_true",
                   help="run the voice gate (fixed threshold vs the relative "
                        "rule); with --replay, on recorded senses")
    p.add_argument("--express", action="store_true",
                   help="run the expression gate: fixed references against the "
                        "field's own usual levels, on a body that senses its own strip")
    p.add_argument("--events", action="store_true",
                   help="run the significant-event gate (pressure rule vs "
                        "surprise rule); with --replay, on recorded senses")
    p.add_argument("--history", action="store_true",
                   help="run the history gate (how long a newborn field needs to "
                        "become indistinguishable from an elder; measures only); "
                        "with --replay and --state, on recorded senses")
    p.add_argument("--battery", action="store_true",
                   help="the battery gate: the cell's voltage sets the reserve's ceiling; "
                        "control against variant through a scripted drain and recharge")
    p.add_argument("--palette", action="store_true",
                   help="the palette gate: what a tone sounds like, the open palette against "
                        "the plain beep")
    p.add_argument("--dark", action="store_true",
                   help="run the dark-room gate: with the rule in mind/calm_v06.py the strip is "
                        "dim and slow in a dark room, at any distance from the light sensor")
    p.add_argument("--colour", action="store_true",
                   help="the colour gate: the strip's hue from the reservoir against the "
                        "blue-to-orange blend")
    p.add_argument("--twin", action="store_true",
                   help="run the twin gate (the Creature is the same with the "
                        "newborn twin beside it, in the field and in the collector)")
    p.add_argument("--history-hours", type=int, default=24,
                   help="hours of test input for --history (default 24)")
    p.add_argument("--raise-hours", type=int, default=48,
                   help="hours the synthetic elder lives before the test, "
                        "for --history without --replay (default 48)")
    p.add_argument("--replay", help="CSV of recorded senses for --events, --voice, --express "
                                    "or --history (tick, logged_at, sound, light, motion, weather)")
    p.add_argument("--state", help="saved field state to start --replay from")
    p.add_argument("--loop-gain", type=float, default=0.3,
                   help="how strongly the body's output returns as input "
                        "(dark-room probe; default 0.3, kept loose to avoid "
                        "runaway)")
    p.add_argument("--curiosity", type=float, default=1.2,
                   help="strength of the boredom-gated probe drive "
                        "(dark-room probe; default 1.2)")
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="override a cell_field_v06 constant for this run")
    p.add_argument("--json", help="save full result (metrics + weight map)")
    p.add_argument("--compare", help="baseline result JSON to diff against")
    p.add_argument("--report-every", type=int, default=REPORT_EVERY_DEFAULT)
    args = p.parse_args()
    if args.exprmem:
        ok = expression_memory_probe(args)
        sys.exit(0 if ok else 1)
    if args.exprbias:
        ok = expression_bias_probe(args)
        sys.exit(0 if ok else 1)
    if args.exprnov:
        ok = expression_novelty_probe(args)
        sys.exit(0 if ok else 1)
    if args.darkroom:
        ok = darkroom_probe(args)
        sys.exit(0 if ok else 1)
    if args.forward:
        ok = forward_probe(args)
        sys.exit(0 if ok else 1)
    if args.events:
        ok = events_probe(args)
        sys.exit(0 if ok else 1)
    if args.history:
        ok = history_probe(args)
        sys.exit(0 if ok else 1)
    if args.twin:
        ok = twin_probe(args)
        sys.exit(0 if ok else 1)
    if args.battery:
        ok = battery_probe(args)
        sys.exit(0 if ok else 1)
    if args.dark:
        ok = dark_probe(args)
        sys.exit(0 if ok else 1)
    if args.colour:
        ok = colour_probe(args)
        sys.exit(0 if ok else 1)
    if args.palette:
        ok = palette_probe(args)
        sys.exit(0 if ok else 1)
    if args.express:
        ok = express_probe(args)
        sys.exit(0 if ok else 1)
    if args.voice:
        ok = voice_probe(args)
        sys.exit(0 if ok else 1)
    if args.feel:
        ok = feel_probe(args)
        sys.exit(0 if ok else 1)
    if args.curious:
        ok = curious_probe(args)
        sys.exit(0 if ok else 1)
    if args.predictive:
        ok = predictive_probe(args)
        sys.exit(0 if ok else 1)
    if args.readout:
        ok = readout_probe(args)
        sys.exit(0 if ok else 1)
    if args.reservoir:
        ok = reservoir_probe(args)
        sys.exit(0 if ok else 1)
    if args.gate:
        ok = gate(args)
        sys.exit(0 if ok else 1)
    run(args)


if __name__ == "__main__":
    main()
