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
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="override a cell_field_v06 constant for this run")
    p.add_argument("--json", help="save full result (metrics + weight map)")
    p.add_argument("--compare", help="baseline result JSON to diff against")
    p.add_argument("--report-every", type=int, default=REPORT_EVERY_DEFAULT)
    args = p.parse_args()
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
