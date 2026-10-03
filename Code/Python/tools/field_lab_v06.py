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
import json
import math
import random
import statistics
import sys
import tempfile
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind import cell_field_v06 as cf
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
                          (350.0, 0.90), (370.0, 0.30), (400.0, 1.20), (440.0, 1.00)]


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
           * (0.85 + 0.3 * room.random()) * muffle)
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


def _history_life(inputs, seed, state_path=None):
    """One life on the test input. Returns A, B and T per tick, and the ring
    weights (sorted by link key) at the start and every HISTORY_WEIGHT_EVERY
    ticks after. The decoder is held to the fixed expression model: the relative
    one reads each field against its own usual, which would hide the difference
    this gate looks for."""
    random.seed(seed)
    field = cf.build_field()
    if state_path and cf.load_field(field, state_path) is None:
        raise SystemExit(f"could not load a field state from {state_path}")
    decoder = ExpressionDecoderV06(knobs={"EXPRESSION_MODEL": "fixed"})
    keys = sorted(field.weights)
    out = {"A": [], "B": [], "T": [], "keys": keys,
           "weights": [[field.weights[k] for k in keys]]}
    for t, values in enumerate(inputs, 1):
        signal = decoder.read(field.step(values))
        out["A"].append(signal["A"])
        out["B"].append(signal["B"])
        out["T"].append(signal["T"])
        if t % HISTORY_WEIGHT_EVERY == 0:
            out["weights"].append([field.weights[k] for k in keys])
    return out


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
        # How far the newborn's weights travelled this hour, sample to sample.
        moved = sum(abs(b - a)
                    for s in range(first, end)
                    for a, b in zip(newborn["weights"][s], newborn["weights"][s + 1]))
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


def _history_compare(current, baseline):
    """Control against variant for the history gate."""
    base, cur = baseline.get("history"), current["history"]
    if not base:
        raise SystemExit("--compare with --history needs a file saved by --history --json")
    print(f"\ncompare vs {baseline.get('source')} "
          f"(v{baseline.get('field_version')}, seed {baseline.get('seed')}, "
          f"overrides {baseline.get('overrides')}):")
    for name, key in (("expression horizon", "expression_horizon"), ("link horizon", "link_horizon")):
        print(f"  {name:32s} {_history_text(base[key], base['hours'])} -> "
              f"{_history_text(cur[key], cur['hours'])}")
    share = cur["newborn_learning_3h"] / base["newborn_learning_3h"] if base["newborn_learning_3h"] else 0.0
    print(f"  {'newborn learning, first 3 hours':32s} {base['newborn_learning_3h']:.3f} -> "
          f"{cur['newborn_learning_3h']:.3f} ({share * 100:.0f}% of the control)")
    print(f"  {'railed links at the end':32s} {base['railed_links']} -> {cur['railed_links']}")
    print(f"  {'mean noise gap':32s} {base['noise_gap_mean']:.4f} -> {cur['noise_gap_mean']:.4f}")
    print("\n  hour | expression gap      | link gap            | railed links")
    for a, b in zip(base["rows"], cur["rows"]):
        print(f"  {a['hour']:4d} | {a['expression_gap']:.4f} -> {b['expression_gap']:.4f}"
              f"    | {a['link_gap']:.4f} -> {b['link_gap']:.4f}"
              f"    | {a['railed_links']:2d} -> {b['railed_links']:2d}")


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
            # Raise the elder, then save it and load it again, so it starts the
            # test the way the saved field does: structure kept, the moment lost.
            random.seed(args.seed)
            raised = cf.build_field()
            for values in scenario_inputs("bursts", args.raise_hours * per,
                                          random.Random(args.seed + 1)):
                raised.step(values)
            state = str(Path(folder) / "elder.json")
            cf.save_field(raised, state)
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
            "railed_links": rows[-1]["railed_links"],
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
        _history_compare(result, json.loads(Path(args.compare).read_text()))
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
