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
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind import cell_field_v06 as cf
from mind.expression_v06 import (
    ExpressionDecoderV06,
    voice_command_from_signal,
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


def clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def expression_vector(signal, speaker_activation):
    """The body's emitted state for this tick, exactly as the decoder made it."""
    a = clamp01(float(signal.get("A", 0.0) or 0.0))
    b = clamp01((float(signal.get("B", 0.0) or 0.0) + 1.0) * 0.5)
    t = clamp01(float(signal.get("T", 0.0) or 0.0))
    voiced = 1.0 if voice_command_from_signal(signal, speaker_activation) else 0.0
    return (a, b, t, voiced)


def quantize_expression(vec, bins):
    """Grid-quantize an expression vector to a node key. `bins` is the
    resolution knob, the spectral radius of this layer: too many and every tick
    is a fresh node, too few and everything collapses to one."""
    a, b, t, voiced = vec

    def q(x):
        return min(bins - 1, int(x * bins))

    return (q(a), q(b), q(t), int(round(voiced)))


def dequantize_expression(node, bins):
    """Node key back to a representative expression vector (bin centres)."""
    qa, qb, qt, qv = node
    return ((qa + 0.5) / bins, (qb + 0.5) / bins, (qt + 0.5) / bins, float(qv))


class ExpressionGraph:
    """An autobiographical graph of the creature's own expressions.

    Nodes are quantized expression states. Edges are transitions between
    consecutive states. `visits`/`count` are cumulative, the life's full tally,
    and drive the identity metric. `node_weight`/`edge_weight` are the same
    tally under slow decay: the live graph, where unused paths fade and prune."""

    def __init__(self, bins=5, decay=0.999, prune=0.01):
        self.bins = int(bins)
        self.decay = float(decay)
        self.prune = float(prune)
        self.visits = {}        # node -> cumulative count
        self.node_weight = {}   # node -> decayed weight
        self.count = {}         # (src, dst) -> cumulative count
        self.edge_weight = {}   # (src, dst) -> decayed weight
        self.prev = None
        self.ticks = 0

    def observe(self, vec):
        node = quantize_expression(vec, self.bins)
        self.ticks += 1
        if self.decay < 1.0:
            for k in list(self.edge_weight):
                w = self.edge_weight[k] * self.decay
                if w < self.prune:
                    del self.edge_weight[k]
                else:
                    self.edge_weight[k] = w
            for k in list(self.node_weight):
                self.node_weight[k] *= self.decay
        self.visits[node] = self.visits.get(node, 0) + 1
        self.node_weight[node] = self.node_weight.get(node, 0.0) + 1.0
        if self.prev is not None:
            edge = (self.prev, node)
            self.count[edge] = self.count.get(edge, 0) + 1
            self.edge_weight[edge] = self.edge_weight.get(edge, 0.0) + 1.0
        self.prev = node

    def visit_entropy(self):
        """Shannon entropy of the node-visit distribution, normalized 0..1.
        Near 0 is a rut (one groove); near 1 is an even smear (no habit). The
        collapse detector step 3 will act on; here it is only a readout."""
        total = sum(self.visits.values())
        if total <= 0 or len(self.visits) <= 1:
            return 0.0
        h = 0.0
        for c in self.visits.values():
            p = c / total
            h -= p * math.log(p, 2)
        return h / math.log(len(self.visits), 2)

    def top_motifs(self, k=5, include_self=True):
        """The most-travelled transitions: the creature's recurring moves. With
        include_self=False, drop dwell (X -> X) and show the actual moves
        between states, which is what a narrative motif is."""
        items = self.count.items()
        if not include_self:
            items = [(e, c) for e, c in items if e[0] != e[1]]
        return sorted(items, key=lambda kv: kv[1], reverse=True)[:k]

    def node_distribution(self):
        total = sum(self.visits.values()) or 1
        return {n: c / total for n, c in self.visits.items()}

    def edge_distribution(self):
        total = sum(self.count.values()) or 1
        return {e: c / total for e, c in self.count.items()}

    def predict_next(self, node):
        """Where the creature usually goes from `node`: the count-weighted
        centroid of its successors, dwell included, since staying put is a habit
        too. None if this state has no recorded future yet."""
        succ = [(dst, c) for (src, dst), c in self.count.items() if src == node]
        if not succ:
            return None
        total = sum(c for _, c in succ)
        acc = [0.0, 0.0, 0.0, 0.0]
        for dst, c in succ:
            vec = dequantize_expression(dst, self.bins)
            for i in range(4):
                acc[i] += vec[i] * c
        return tuple(a / total for a in acc)


def graph_distance(g1, g2):
    """Total-variation distance between two autobiographies, 0..1.

    Treat each graph as a distribution over nodes and over edges; average the
    two distances. 0 means identical histories, 1 means no shared expression at
    all. This is the identity metric: two creatures with different lives should
    sit far apart, two with the same kind of life close."""

    def tv(d1, d2):
        keys = set(d1) | set(d2)
        return 0.5 * sum(abs(d1.get(k, 0.0) - d2.get(k, 0.0)) for k in keys)

    node_tv = tv(g1.node_distribution(), g2.node_distribution())
    edge_tv = tv(g1.edge_distribution(), g2.edge_distribution())
    return 0.5 * (node_tv + edge_tv)


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


def _novelty_target(graph, vec, bins):
    """Aim at the least-explored expression state next to where bias wants to
    go. Look at the grid neighbours (one step on each axis, plus a voiced flip)
    and pick the one visited least so far. Directed exploration, not a twitch."""
    node = quantize_expression(vec, bins)
    qa, qb, qt, qv = node
    candidates = [node, (qa, qb, qt, 1 - qv)]
    for i, q in enumerate((qa, qb, qt)):
        for step in (-1, 1):
            nq = q + step
            if 0 <= nq < bins:
                cand = list(node)
                cand[i] = nq
                candidates.append(tuple(cand))
    best = min(candidates, key=lambda c: (graph.visits.get(c, 0), c))
    return dequantize_expression(best, bins)


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
