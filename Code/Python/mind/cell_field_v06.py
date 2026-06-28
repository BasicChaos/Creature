"""
Cell field (v06.1 - the twelve-cell ring).

Build step 1 of the v06 design (see `Creature v06.md`): shrink the body to a
ring of twelve outer cells, keeping the v05.4 leaky-integrator cell and its full
machinery (metabolism, sleep/replay, homeostasis, fatigue, ripple, Hebbian).
The reservoir (step 2) and the predictive cell (step 4) are NOT here yet. This
is the honest control the later steps must beat on the entropy test.

What changed from v05.4 (`cell_field.py`):

* topology is a ring, not a 111-cell 2D grid. Twelve cells, each wired to its
  two ring neighbors. Distance is ring distance, not Chebyshev.
* six anchors alternate with six in-between cells, in the design's order. Two
  in-between cells are the loop made internal (Speaker x Sound, LED x Light),
  two are the correlated sense pairs (Sound x Motion, Light x Weather), one is
  the weak gap (Weather x Speaker).
* four senses (sound, light, motion, weather) and two emitters (speaker, led),
  up from two senses and one emitter.

The loop is NOT closed here. The strip-facing light sensor and speaker-facing
mic are a firmware-integration step (v06 step 7). Step 1 drives the four senses
as ordinary external inputs and checks the ring shapes structure where the
anatomy says it should.

Constants are carried over from v05.4 unchanged. They were tuned for a 111-cell
field at 1 Hz; on twelve cells some will want a sweep. Judge any change with
tools/field_lab_v06.py, control vs variant, before trusting it.
"""

import json
import math
import os
import random
from datetime import datetime

CELL_COUNT = 12          # outer ring; six inner reservoir cells are separate
SNAPSHOT_VERSION = 1
FIELD_VERSION = "v06.5-predictive"

# The ring, in design order (Creature v06.md, "The field: a ring of twelve").
# Each entry: (label, cell_type, hardware_id). Cell ids are the list indices,
# 0..11. Anchors sit on even positions, in-between cells on odd positions.
#
#   0  Speaker      (emitter)
#   1  Speaker x Sound      <- loop cell: hears its own voice
#   2  Sound        (sense)
#   3  Sound x Motion       <- correlated pair
#   4  Motion       (sense)
#   5  Motion x LED
#   6  LED strip    (emitter)
#   7  LED x Light          <- loop cell: sees its own light
#   8  Light        (sense)
#   9  Light x Weather      <- correlated pair
#   10 Weather      (sense)
#   11 Weather x Speaker    <- the one weak gap
RING = [
    ("speaker",          "anchor_emitter", "emitter_speaker"),
    ("speaker_x_sound",  "free",           None),
    ("sound",            "anchor_sensor",  "sound"),
    ("sound_x_motion",   "free",           None),
    ("motion",           "anchor_sensor",  "motion"),
    ("motion_x_led",     "free",           None),
    ("led",              "anchor_emitter", "emitter_led"),
    ("led_x_light",      "free",           None),
    ("light",            "anchor_sensor",  "light"),
    ("light_x_weather",  "free",           None),
    ("weather",          "anchor_sensor",  "weather"),
    ("weather_x_speaker","free",           None),
]

SENSES = ("sound", "light", "motion", "weather")

# sense name -> ring position of its anchor
SENSE_ANCHOR = {label: n for n, (label, _t, _h) in enumerate(RING) if label in SENSES}
# the two emitter anchor positions (speaker, led)
EMITTER_ANCHORS = [n for n, (_l, t, _h) in enumerate(RING) if t == "anchor_emitter"]

# Named in-between cells, used by the build gate and the dashboard.
LOOP_CELLS = [1, 7]          # Speaker x Sound, LED x Light
CORRELATED_CELLS = [3, 9]    # Sound x Motion, Light x Weather
WEAK_GAP_CELL = 11           # Weather x Speaker

# The six in-between cells (the "free" cells) drive the reservoir as input.
IN_BETWEEN_CELLS = [n for n, (_l, t, _h) in enumerate(RING) if t == "free"]

# --- the reservoir (v06 step 2) -------------------------------------------
# Six inner cells: a fixed recurrent substrate (echo state network), not a ring
# of leaky integrators. Weights are random, set once at init, never touched by
# Hebbian learning. The outer in-between cells drive it; it holds a fading,
# high-dimensional echo of recent history. In step 2 it is read-only: the
# trained readout from reservoir to the two emitters is step 3.
#
# Why fixed: a Hebbian interior homogenizes the same way the outer field did.
# Fixing the weights removes that pressure. Richness comes from the random
# sparse connectivity plus the echo state property: any sufficiently varied
# input history produces a distinguishable reservoir state.
RESERVOIR_ENABLED = True
RESERVOIR_SIZE = 6
# Spectral radius of the recurrent matrix. Must be < 1.0 for the echo state
# property (fading memory: the reservoir forgets its initial state and depends
# only on the input history). ~0.9 is the design's first guess; sweep it.
RESERVOIR_SPECTRAL_RADIUS = 0.9
RESERVOIR_INPUT_SCALE = 0.6     # magnitude of the input weights W_in
RESERVOIR_CONNECTIVITY = 0.5    # fraction of nonzero recurrent weights (sparse)
RESERVOIR_LEAK = 1.0            # leaky-ESN rate; 1.0 = no leak, <1 = slower echo
# The reservoir substrate is drawn from its own fixed stream so it is identical
# across runs regardless of the field/input seed. Two creatures share a body
# plan; what differs is the life they live, i.e. the input history.
RESERVOIR_SEED = 0xC0FFEE

# --- the readout (v06 step 3) ---------------------------------------------
# Each emitter reads a trained linear combination of the reservoir state. The
# reservoir never changes; only these readout weights learn. They train online
# by the delta rule against one-step prediction error: the emitter expresses a
# value, and the loop-partner sense at the next tick is the target the emitter
# was trying to predict (speaker -> sound, led -> light). Error drives the
# update. This is the seed of the predictive direction (step 4) and the closed
# loop (step 7); for now the readout output is computed and trained but does
# NOT feed back into the ring.
READOUT_ENABLED = True
READOUT_LR = 0.01
# emitter hardware id -> the sense it predicts (its ring-adjacent loop partner)
READOUT_TARGET = {"emitter_speaker": "sound", "emitter_led": "light"}

# --- the cell model (v06 step 4) ------------------------------------------
# "leaky"      : the v05.4 leaky integrator. Activation tracks the input level,
#                and a homeostatic gain drives every cell's average activation
#                to one shared target. That equalizer is the homogenizer: under
#                steady input the whole field flattens to the same value.
# "predictive" : each cell holds a running prediction of its own drive and
#                emits the error (surprise), not the level. A cell that predicts
#                well goes quiet; a surprised cell speaks. No shared target, so
#                nothing actively erases differences between cells. The unit is
#                prediction error, not a firing rate. This is the negentropy
#                cell: it builds a model and feeds on surprise.
CELL_MODEL = "predictive"
PRED_BETA = 0.05      # how fast each cell learns its input prediction
PRED_GAIN = 1.30      # surprise -> activation gain
PRED_MAX = 3.0        # clamp on the stored prediction

# --- activation math (unchanged from v05.4) -------------------------------
DECAY_RATE = 0.90
RESTING_ACTIVATION_DECAY = 0.965
DORMANT_ACTIVATION_DECAY = 0.985
DEEP_SLEEP_ACTIVATION_DECAY = 0.992
NOISE_FLOOR = 0.0007

# --- ripple math (unchanged) ----------------------------------------------
RIPPLE_DECAY = 0.82
RIPPLE_VELOCITY_DECAY = 0.58
RIPPLE_SPREAD = 0.42
SENSOR_RIPPLE_GAIN = 0.65
SLEEP_SENSOR_SCALE = 0.35
ANCHOR_RIPPLE_DRIVE = 0.035
RIPPLE_PRESSURE_GAIN = 0.55
RIPPLE_TO_ACTIVATION = 0.025
RIPPLE_CLAMP = 1.25

# --- wake and dormancy (unchanged) ----------------------------------------
ACTIVE_ACTIVATION_THRESHOLD = 0.06
ACTIVE_RIPPLE_THRESHOLD = 0.08
ACTIVE_PRESSURE_THRESHOLD = 0.09
WAKE_PRESSURE_THRESHOLD = 0.065
WAKE_RIPPLE_THRESHOLD = 0.045
DORMANT_AFTER_TICKS = 75
DEEP_SLEEP_AFTER_TICKS = 300
EXPLORATION_WAKE_CHANCE = 0.0007
DEEP_SLEEP_WAKE_CHANCE = 0.00008

# --- metabolism (unchanged from v05.4, except the shared-reserve sizing
# below, which is scaled to the smaller body) ------------------------------
CELL_ENERGY_INIT = 0.55
CELL_ENERGY_MAX = 1.0
CELL_ENERGY_FLOOR_TO_FIRE = 0.035
# v05.4 sized the reserve for ~111 cells, most of them dormant. On twelve cells
# the ring keeps nearly all of them active (emitters never sleep, neighbours
# stay stimulated), so aggregate draw is dominated by ~12 active refills
# (ACTIVE_CELL_REFILL * 12 ~= 0.19/tick) plus activation and learning costs.
# Replenish must comfortably exceed that or the reserve deadlocks at zero and
# all learning starves (the v05.4.1 bug). These keep the reserve healthy so
# step 1 tests topology, not metabolism. field_lab_v06 can sweep them.
GLOBAL_ENERGY_INIT = 4.0
GLOBAL_ENERGY_MAX = 6.0
GLOBAL_REPLENISH_PER_TICK = 0.6
ACTIVE_CELL_REFILL = 0.016
RESTING_CELL_REFILL = 0.008
DORMANT_CELL_REFILL = 0.003
DEEP_SLEEP_CELL_REFILL = 0.0015
BASE_UPDATE_COST = 0.0025
ACTIVATION_COST = 0.032
RIPPLE_COST = 0.010
LEARNING_COST = 0.004
EMITTER_COST = 0.014
LOW_ENERGY_THRESHOLD = 0.14
DEEP_SLEEP_ENERGY_THRESHOLD = 0.06
WAKE_ENERGY_THRESHOLD = 0.20
FATIGUE_GAIN = 0.022
FATIGUE_PRESSURE_GAIN = 0.018
FATIGUE_RECOVERY = 0.010
FATIGUE_SLEEP_RECOVERY = 0.020
FATIGUE_REST_THRESHOLD = 0.44
FATIGUE_SLEEP_THRESHOLD = 0.72

# --- structural memory (unchanged) ----------------------------------------
HEBBIAN_ENABLED = True
ETA = 0.006
REPLAY_ETA = 0.018
W_MAX = 2.0
STRUCTURAL_DECAY = 0.00001
UNUSED_DECAY = 0.0004
COACTIVITY_THRESHOLD = 0.0012
PRESSURE_LEARNING_THRESHOLD = 0.025
PRESSURE_LEARNING_GAIN = 0.65
PRESSURE_ASSOC_DECAY = 0.99999
PRESSURE_ASSOC_GAIN = 0.020
PRUNE_FLOOR = 0.02
LIVE_LINK_THRESHOLD = 0.05
PRUNE_AFTER_INACTIVE_TICKS = 3600
CONSOLIDATION_UNUSED_KEEP = 0.995
CONSOLIDATION_WEAK_KEEP = 0.998

# --- sleep and replay ------------------------------------------------------
LOW_STIMULATION_TICKS = 160
SLEEP_DURATION_TICKS = 30
SLEEP_COOLDOWN_TICKS = 240
# Raised from 0.055 on 2026-06-28. The weather anchor keeps a steady, non-zero
# pressure on itself and its ring neighbors at all times, and the v06.5 ring
# logged a median top-cell pressure around 0.6 overnight with a literally
# silent room (sound/light/motion all 0.00). 0.055 could never be true, so
# quiet_ticks never accumulated and the field never slept. 0.7 sits just
# above the calm-period ceiling (p90 ~0.62-0.68 during quiet hours) while
# staying below sustained real activity (median ~0.9-1.17 in the one evening
# stretch with real sound/light/motion in the same log).
QUIET_PRESSURE_THRESHOLD = 0.7
QUIET_MOTION_THRESHOLD = 0.015
MEMORY_PRESSURE_SLEEP_THRESHOLD = 0.58
# scaled to the smaller reserve (was 7.5 against a 36-unit pool)
LOW_RESERVE_SLEEP_THRESHOLD = 0.9
RECENT_EVENT_LIMIT = 80
SIGNIFICANT_PRESSURE_THRESHOLD = 0.18
SIGNIFICANT_DELTA_THRESHOLD = 0.16
REPLAY_EVENTS_PER_TICK = 2

# --- homeostatic layer (unchanged) ----------------------------------------
HOMEO_ENABLED = True
HOMEO_TARGET = 0.06
HOMEO_AVG_RATE = 0.01
HOMEO_RATE = 0.018
HOMEO_GAIN_INIT = 0.22
HOMEO_GAIN_MIN = 0.0
HOMEO_GAIN_MAX = 4.0

# Sensor influence falls off with ring distance. Same table as v05.4; on a
# 12-ring the max distance is 6, so the far tail is naturally truncated.
SENSOR_WEIGHT_BY_DISTANCE = {
    0: 1.00,
    1: 0.52,
    2: 0.24,
    3: 0.10,
    4: 0.04,
}

# Ring connectivity. Radius 1 on the ring = the two immediate neighbors.
CONNECTION_RADIUS = 1
CONNECTION_WEIGHT_BY_DISTANCE = {1: 0.20}
NEIGHBOR_ACTIVATION_GAIN = 0.45
NEIGHBOR_DRIVE_CLAMP = 1.5
EMITTER_BODY_PRESSURE_GAIN = 1.15


def clamp(value, low, high):
    return max(low, min(high, value))


# --- small dense linear algebra (pure stdlib, so results match on any machine
# and the "only the random module" determinism contract holds) -------------

def _matvec(matrix, vec):
    return [sum(row[k] * vec[k] for k in range(len(vec))) for row in matrix]


def _norm(vec):
    return math.sqrt(sum(x * x for x in vec)) if vec else 0.0


def spectral_radius(matrix, iters=400):
    """Estimate the spectral radius (max |eigenvalue|) by power iteration.

    Uses the growth-rate form r = lim ||A^k x||^(1/k), accumulated in log space
    so it stays stable for any matrix, including ones whose dominant eigenvalue
    is a complex pair (where plain power iteration oscillates but the magnitude
    growth still converges). Deterministic: fixed start vector.
    """
    n = len(matrix)
    rng = random.Random(12345)
    v = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    norm = _norm(v) or 1.0
    v = [x / norm for x in v]
    log_growth = 0.0
    counted = 0
    for step in range(iters):
        v = _matvec(matrix, v)
        norm = _norm(v)
        if norm == 0.0:
            return 0.0
        if step >= iters // 4:  # burn-in before counting
            log_growth += math.log(norm)
            counted += 1
        v = [x / norm for x in v]
    return math.exp(log_growth / counted) if counted else 0.0


def ring_distance(i, j):
    """Steps around the ring between two cells (the shorter way)."""
    d = abs(i - j)
    return min(d, CELL_COUNT - d)


def sensor_weight(distance):
    return SENSOR_WEIGHT_BY_DISTANCE.get(distance, 0.0)


def map_legacy_state(state):
    return {
        "active": "active",
        "light_sleep": "resting",
        "resting": "resting",
        "dormant": "dormant",
        "deep_sleep": "deep_sleep",
    }.get(state, "resting")


class FieldCell:
    """One ring cell. Long-term memory is derived from links, not stored here."""

    def __init__(self, n, label, cell_type, hardware_id=None):
        self.n = n
        self.name = f"C{n}"
        self.label = label
        self.type = cell_type
        self.hardware_id = hardware_id

        # Fast operational state.
        self.activation = 0.0
        self.pressure = 0.0
        self.ripple = 0.0
        self.ripple_velocity = 0.0
        self.prediction = 0.0   # predictive cell: running estimate of its drive
        self.surprise = 0.0     # predictive cell: last prediction error magnitude

        # Metabolic state.
        self.energy = CELL_ENERGY_INIT if not hardware_id else 0.72
        self.fatigue = 0.0
        self.relevance = 0.32 if hardware_id else 0.08

        # Slow operational traits.
        self.size = 1.0
        self.age = 0
        self.health = 1.0
        self.threshold = 1.0
        self.avg_activation = HOMEO_TARGET
        self.homeo_gain = HOMEO_GAIN_INIT

        # Scheduling and dormancy.
        self.state = "active" if hardware_id else "resting"
        self.activation_threshold = WAKE_PRESSURE_THRESHOLD
        self.last_active_tick = 0
        self.last_impulse_tick = 0
        self.last_tick = 0
        self.tick_interval = self._default_tick_interval()
        self.wake_sensitivity = 1.0

        # Developmental flags (unused until growth/mitosis land).
        self.can_move = cell_type == "free"
        self.can_divide = True
        self.can_die = cell_type == "free"

    def _default_tick_interval(self):
        if self.hardware_id:
            return 1
        # distance to the nearest anchor (sense or emitter) around the ring
        anchors = list(SENSE_ANCHOR.values()) + EMITTER_ANCHORS
        d_anchor = min(ring_distance(self.n, a) for a in anchors)
        if d_anchor <= 1:
            return 2
        if d_anchor <= 2:
            return 4
        return 8 + (self.n % 3)


class CellField:
    """The twelve-cell ring: v05.4 dynamics on the v06 anatomy."""

    def __init__(self):
        self.cells = {}
        self.weights = {}
        self.connection_age = {}
        self.connection_usage = {}
        self.connection_pressure = {}
        self.connection_last_active_tick = {}
        # per-sense, per-cell injection weight
        self.sense_weight = {s: {} for s in SENSES}
        self.tick_count = 0
        self.last_sense = {s: 0.0 for s in SENSES}

        self.energy_reserve = GLOBAL_ENERGY_INIT
        self.memory_pressure = 0.0
        self.sleep_mode = "awake"
        self.sleep_ticks_remaining = 0
        self.last_sleep_tick = -SLEEP_COOLDOWN_TICKS
        self.quiet_ticks = 0
        self.recent_events = []
        self.last_events = []
        self.last_sleep_summary = None
        self.last_consolidation = {
            "links_pruned": 0,
            "links_reinforced": 0,
            "events_reviewed": 0,
        }
        self._current_sleep = None

        # Reservoir substrate (fixed at init, never learned). Built before the
        # ring so it is ready when the first step reads the in-between cells.
        self.reservoir_state = [0.0] * RESERVOIR_SIZE
        self.reservoir_input = [0.0] * len(IN_BETWEEN_CELLS)
        self.reservoir_W = []
        self.reservoir_Win = []
        self.reservoir_radius = 0.0
        self._build_reservoir()

        # Readout weights: one vector per emitter over the reservoir state plus
        # a bias term. Trained by the delta rule; the reservoir stays fixed.
        self.readout_w = {hw: [0.0] * (RESERVOIR_SIZE + 1) for hw in READOUT_TARGET}
        self.readout_output = {hw: 0.0 for hw in READOUT_TARGET}

        self._build()

    def _build_reservoir(self):
        """Random sparse recurrent matrix scaled to RESERVOIR_SPECTRAL_RADIUS,
        plus dense input weights. Drawn from a fixed stream (RESERVOIR_SEED) so
        the body plan is identical across runs; only the lived input differs."""
        if not RESERVOIR_ENABLED:
            return
        rng = random.Random(RESERVOIR_SEED)
        size = RESERVOIR_SIZE
        n_in = len(IN_BETWEEN_CELLS)

        W = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(size):
                if rng.random() < RESERVOIR_CONNECTIVITY:
                    W[i][j] = rng.uniform(-1.0, 1.0)

        measured = spectral_radius(W)
        if measured > 0.0:
            scale = RESERVOIR_SPECTRAL_RADIUS / measured
            W = [[w * scale for w in row] for row in W]
        self.reservoir_W = W
        self.reservoir_radius = round(spectral_radius(W), 5)

        self.reservoir_Win = [
            [rng.uniform(-1.0, 1.0) * RESERVOIR_INPUT_SCALE for _ in range(n_in)]
            for _ in range(size)
        ]

    def reservoir_step(self, state, inp):
        """One leaky-ESN update. Pure function of (state, input); the matrices
        are fixed. r <- (1-leak) r + leak * tanh(W r + W_in u)."""
        pre = _matvec(self.reservoir_W, state)
        drive = _matvec(self.reservoir_Win, inp)
        leak = RESERVOIR_LEAK
        return [
            (1.0 - leak) * state[i] + leak * math.tanh(pre[i] + drive[i])
            for i in range(RESERVOIR_SIZE)
        ]

    def _update_readout(self, values):
        """Delta-rule readout from the reservoir to each emitter.

        The emitter expresses a linear read of the reservoir state and learns,
        by the delta rule, to track its loop-partner sense (speaker -> sound,
        led -> light): output = w . [r, 1], error = sense - output,
        w += lr * error * [r, 1]. The reservoir is untouched; only the readout
        learns, and the output does not yet feed back into the ring (step 7).

        Target is the current coupled sense, the field state the emitter should
        express. Once the loop is physically closed (step 7) the same rule
        becomes genuine prediction: the sense it confirms is the emitter's own
        returning output."""
        x = self.reservoir_state + [1.0]  # bias term
        for hw, target_sense in READOUT_TARGET.items():
            w = self.readout_w[hw]
            out = sum(w[k] * x[k] for k in range(len(w)))
            error = values.get(target_sense, 0.0) - out
            for k in range(len(w)):
                w[k] += READOUT_LR * error * x[k]
            self.readout_output[hw] = out

    def _build(self):
        for n, (label, cell_type, hardware_id) in enumerate(RING):
            self.cells[n] = FieldCell(n, label, cell_type, hardware_id)

        for s in SENSES:
            anchor = SENSE_ANCHOR[s]
            for n in range(CELL_COUNT):
                self.sense_weight[s][n] = sensor_weight(ring_distance(n, anchor))

        # Ring edges: each cell to its next neighbor, closing 11 -> 0.
        for i in range(CELL_COUNT):
            j = (i + 1) % CELL_COUNT
            key = (i, j) if i < j else (j, i)
            self.weights[key] = CONNECTION_WEIGHT_BY_DISTANCE[1]
            self.connection_age[key] = 0
            self.connection_usage[key] = 0
            self.connection_pressure[key] = 0.0
            self.connection_last_active_tick[key] = -1

    def weight(self, i, j):
        if i == j:
            return 0.0
        key = (i, j) if i < j else (j, i)
        return self.weights.get(key, 0.0)

    @property
    def emitter_activation(self):
        """Mean activation across the two emitter anchors (speaker + led)."""
        return sum(self.cells[a].activation for a in EMITTER_ANCHORS) / len(EMITTER_ANCHORS)

    def emitter_activations(self):
        return {self.cells[a].label: round(self.cells[a].activation, 4) for a in EMITTER_ANCHORS}

    def _state_counts(self):
        counts = {"active": 0, "resting": 0, "dormant": 0, "deep_sleep": 0}
        for cell in self.cells.values():
            counts[cell.state] = counts.get(cell.state, 0) + 1
        return counts

    def _metabolism_summary(self):
        cells = list(self.cells.values())
        live_links = sum(1 for w in self.weights.values() if w > LIVE_LINK_THRESHOLD)
        pruned_links = len(self.weights) - live_links
        railed = sum(1 for c in cells if c.homeo_gain >= HOMEO_GAIN_MAX * 0.999)
        return {
            "mode": self.sleep_mode,
            "energy_reserve": round(self.energy_reserve, 4),
            "energy_reserve_max": GLOBAL_ENERGY_MAX,
            "energy_avg": round(sum(c.energy for c in cells) / len(cells), 4),
            "fatigue_avg": round(sum(c.fatigue for c in cells) / len(cells), 4),
            "memory_pressure": round(self.memory_pressure, 4),
            "quiet_ticks": self.quiet_ticks,
            "sleep_ticks_remaining": self.sleep_ticks_remaining,
            "last_sleep_tick": self.last_sleep_tick,
            "recent_event_count": len(self.recent_events),
            "live_connections": live_links,
            "pruned_connections": pruned_links,
            "total_connections": len(self.weights),
            "homeo_gain_railed": railed,
            **self.last_consolidation,
        }

    def _replenish_energy(self):
        self.energy_reserve = clamp(
            self.energy_reserve + GLOBAL_REPLENISH_PER_TICK,
            0.0,
            GLOBAL_ENERGY_MAX,
        )

        def priority(cell):
            recovery_debt = max(0.0, WAKE_ENERGY_THRESHOLD - cell.energy)
            recovering = 1 if recovery_debt > 0.0 else 0
            role = 4 if cell.hardware_id else 0
            state = {"active": 3, "resting": 2, "dormant": 1, "deep_sleep": 0}[cell.state]
            return (recovering, role, recovery_debt, cell.relevance, -cell.fatigue, state)

        for cell in sorted(self.cells.values(), key=priority, reverse=True):
            if self.energy_reserve <= 0.0:
                break
            if cell.hardware_id:
                rate = ACTIVE_CELL_REFILL
            elif cell.state == "active":
                rate = ACTIVE_CELL_REFILL
            elif cell.state == "resting":
                rate = RESTING_CELL_REFILL
            elif cell.state == "dormant":
                rate = DORMANT_CELL_REFILL
            else:
                rate = DEEP_SLEEP_CELL_REFILL

            relevance_bonus = 0.55 + 0.45 * clamp(cell.relevance, 0.0, 1.0)
            request = min(CELL_ENERGY_MAX - cell.energy, rate * relevance_bonus)
            draw = min(request, self.energy_reserve)
            cell.energy += draw
            self.energy_reserve -= draw

    def _spend_cell_energy(self, cell, amount):
        if amount <= 0.0:
            return 1.0
        available = min(cell.energy, amount)
        cell.energy -= available
        return clamp(available / amount, 0.0, 1.0)

    def _spend_reserve(self, amount):
        if amount <= 0.0:
            return 1.0
        available = min(self.energy_reserve, amount)
        self.energy_reserve -= available
        return clamp(available / amount, 0.0, 1.0)

    def _should_update_cell(self, n, pressure, ripple):
        cell = self.cells[n]
        if cell.fatigue >= FATIGUE_SLEEP_THRESHOLD:
            return False
        if cell.hardware_id:
            return True
        if pressure >= cell.activation_threshold:
            return True
        if abs(ripple) >= WAKE_RIPPLE_THRESHOLD * cell.wake_sensitivity:
            return True
        if self.tick_count % max(1, cell.tick_interval) == 0:
            return True
        chance = DEEP_SLEEP_WAKE_CHANCE if cell.state == "deep_sleep" else EXPLORATION_WAKE_CHANCE
        return random.random() < chance

    def _activation_decay_for(self, cell):
        if cell.state == "deep_sleep":
            return DEEP_SLEEP_ACTIVATION_DECAY
        if cell.state == "dormant":
            return DORMANT_ACTIVATION_DECAY
        if cell.state == "resting":
            return RESTING_ACTIVATION_DECAY
        return DECAY_RATE

    def _set_cell_state(self, cell):
        active_signal = (
            cell.activation >= ACTIVE_ACTIVATION_THRESHOLD
            or abs(cell.ripple) >= ACTIVE_RIPPLE_THRESHOLD
            or cell.pressure >= ACTIVE_PRESSURE_THRESHOLD
            or cell.hardware_id is not None
        )

        if active_signal and cell.fatigue < FATIGUE_SLEEP_THRESHOLD:
            cell.state = "active"
            cell.last_active_tick = self.tick_count
        else:
            quiet_for = self.tick_count - cell.last_active_tick
            if (
                cell.fatigue >= FATIGUE_SLEEP_THRESHOLD
                or quiet_for >= DEEP_SLEEP_AFTER_TICKS
            ):
                cell.state = "deep_sleep"
            elif quiet_for >= DORMANT_AFTER_TICKS or cell.energy < LOW_ENERGY_THRESHOLD:
                cell.state = "dormant"
            else:
                cell.state = "resting"

        cell.tick_interval = self._tick_interval_for(cell)

    def _tick_interval_for(self, cell):
        if cell.hardware_id:
            return 1
        relevance = clamp(cell.relevance, 0.0, 1.0)
        energy_penalty = 1.35 if cell.energy < LOW_ENERGY_THRESHOLD else 1.0
        if cell.state == "active":
            base = 1
        elif cell.state == "resting":
            base = 2 + int((1.0 - relevance) * 5)
        elif cell.state == "dormant":
            base = 12 + int((1.0 - relevance) * 34)
        else:
            base = 45 + int((1.0 - relevance) * 75)
        return max(1, int(base * energy_penalty))

    def _record_significant_event(self, sensed, deltas):
        ranked = sorted(
            self.cells.values(),
            key=lambda c: c.pressure + abs(c.ripple) + c.activation,
            reverse=True,
        )
        top_cells = [
            {
                "n": c.n,
                "pressure": round(c.pressure, 4),
                "activation": round(c.activation, 4),
                "ripple": round(c.ripple, 4),
            }
            for c in ranked[:5]
        ]
        significance = max(
            (c["pressure"] + abs(c["ripple"]) + c["activation"]) for c in top_cells
        )
        if (
            significance < SIGNIFICANT_PRESSURE_THRESHOLD
            and max(deltas.values()) < SIGNIFICANT_DELTA_THRESHOLD
        ):
            return

        event = {
            "tick": self.tick_count,
            "type": "pressure_spike",
            "significance": round(significance, 4),
            "senses": {s: round(sensed[s], 4) for s in SENSES},
            "deltas": {s: round(deltas[s], 4) for s in SENSES},
            "cells": top_cells,
        }
        self.recent_events.append(event)
        if len(self.recent_events) > RECENT_EVENT_LIMIT:
            self.recent_events = self.recent_events[-RECENT_EVENT_LIMIT:]
        self.last_events.append(event)

    def _update_relevance(self):
        weight_total = {n: 0.0 for n in self.cells}
        pressure_total = {n: 0.0 for n in self.cells}
        usage_total = {n: 0.0 for n in self.cells}
        degree = {n: 0 for n in self.cells}
        for (i, j), w in self.weights.items():
            assoc = self.connection_pressure.get((i, j), 0.0)
            usage = min(1.0, self.connection_usage.get((i, j), 0) / 120.0)
            for n in (i, j):
                weight_total[n] += max(0.0, w)
                pressure_total[n] += assoc
                usage_total[n] += usage
                degree[n] += 1

        for n, cell in self.cells.items():
            if degree[n] == 0:
                continue
            target = (
                0.52 * clamp((weight_total[n] / degree[n]) / 0.35, 0.0, 1.0)
                + 0.32 * clamp(pressure_total[n] / degree[n], 0.0, 1.0)
                + 0.16 * clamp(usage_total[n] / degree[n], 0.0, 1.0)
            )
            if cell.hardware_id:
                target = max(target, 0.35)
            cell.relevance += (target - cell.relevance) * 0.08
            cell.relevance = clamp(cell.relevance, 0.0, 1.0)

    def _update_memory_pressure(self):
        live_weights = [w for w in self.weights.values() if w > LIVE_LINK_THRESHOLD]
        total = len(self.weights)
        weak = sum(1 for w in live_weights if w < 0.075)
        stale = 0
        for key, w in self.weights.items():
            if w <= LIVE_LINK_THRESHOLD:
                continue
            last = self.connection_last_active_tick.get(key, -1)
            inactive_for = self.tick_count - last if last >= 0 else self.tick_count
            if inactive_for > PRUNE_AFTER_INACTIVE_TICKS:
                stale += 1

        weak_fraction = weak / total if total else 0.0
        stale_fraction = stale / total if total else 0.0
        event_pressure = len(self.recent_events) / RECENT_EVENT_LIMIT
        sleep_age = clamp((self.tick_count - self.last_sleep_tick) / 1800.0, 0.0, 1.0)
        reserve_pressure = 1.0 - clamp(self.energy_reserve / GLOBAL_ENERGY_MAX, 0.0, 1.0)
        self.memory_pressure = clamp(
            0.36 * weak_fraction
            + 0.28 * stale_fraction
            + 0.18 * event_pressure
            + 0.10 * sleep_age
            + 0.08 * reserve_pressure,
            0.0,
            1.0,
        )

    def _maybe_enter_sleep(self, deltas):
        if self.sleep_mode == "sleep":
            return
        if self.tick_count - self.last_sleep_tick < SLEEP_COOLDOWN_TICKS:
            return

        sensor_motion = max(deltas.values())
        max_pressure = max(c.pressure for c in self.cells.values())
        if sensor_motion < QUIET_MOTION_THRESHOLD and max_pressure < QUIET_PRESSURE_THRESHOLD:
            self.quiet_ticks += 1
        else:
            self.quiet_ticks = 0

        reasons = []
        if self.quiet_ticks >= LOW_STIMULATION_TICKS:
            reasons.append("low_stimulation")
        if self.memory_pressure >= MEMORY_PRESSURE_SLEEP_THRESHOLD:
            reasons.append("memory_pressure")
        if self.energy_reserve <= LOW_RESERVE_SLEEP_THRESHOLD:
            reasons.append("low_energy")

        if not reasons:
            return

        self.sleep_mode = "sleep"
        self.sleep_ticks_remaining = SLEEP_DURATION_TICKS
        self._current_sleep = {
            "start_tick": self.tick_count,
            "reason": "+".join(reasons),
            "events_available": len(self.recent_events),
            "events_reviewed": 0,
            "links_reinforced": 0,
            "links_pruned": 0,
            "energy_before": round(self.energy_reserve, 4),
            "memory_pressure_before": round(self.memory_pressure, 4),
        }

    def _sleep_maintenance(self):
        if not self._current_sleep:
            return

        reviewed = 0
        reinforced = 0
        pruned = 0
        events = sorted(
            self.recent_events,
            key=lambda e: (e.get("significance", 0.0), e.get("tick", 0)),
            reverse=True,
        )[:REPLAY_EVENTS_PER_TICK]

        replayed_ids = {id(e) for e in events}
        self.recent_events = [
            e for e in self.recent_events if id(e) not in replayed_ids
        ]

        for event in events:
            reviewed += 1
            cells = [c["n"] for c in event.get("cells", [])[:4]]
            significance = clamp(event.get("significance", 0.0), 0.0, 1.0)
            for idx, i in enumerate(cells):
                for j in cells[idx + 1:]:
                    key = (i, j) if i < j else (j, i)
                    if key not in self.weights:
                        continue
                    energy_scale = self._spend_reserve(LEARNING_COST * 0.5)
                    if energy_scale <= 0.0:
                        continue
                    assoc = self.connection_pressure.get(key, 0.0)
                    gain = REPLAY_ETA * significance * (0.55 + assoc) * energy_scale
                    self.weights[key] = clamp(self.weights[key] + gain, 0.0, W_MAX)
                    self.connection_usage[key] = self.connection_usage.get(key, 0) + 1
                    self.connection_last_active_tick[key] = self.tick_count
                    self.connection_pressure[key] = clamp(
                        assoc + (significance - assoc) * PRESSURE_ASSOC_GAIN,
                        0.0,
                        1.0,
                    )
                    reinforced += 1

        for key, w in list(self.weights.items()):
            if w <= PRUNE_FLOOR:
                continue
            usage = self.connection_usage.get(key, 0)
            assoc = self.connection_pressure.get(key, 0.0)
            last = self.connection_last_active_tick.get(key, -1)
            inactive_for = self.tick_count - last if last >= 0 else self.tick_count
            protected = (
                0.40 * min(1.0, usage / 80.0)
                + 0.45 * assoc
                + 0.15 * (1.0 if inactive_for < PRUNE_AFTER_INACTIVE_TICKS else 0.0)
            )
            if protected < 0.12:
                w *= CONSOLIDATION_UNUSED_KEEP
            elif w < 0.09 and protected < 0.32:
                w *= CONSOLIDATION_WEAK_KEEP
            if (
                w < LIVE_LINK_THRESHOLD
                and inactive_for >= PRUNE_AFTER_INACTIVE_TICKS
            ):
                w = PRUNE_FLOOR
                pruned += 1
            self.weights[key] = max(w, PRUNE_FLOOR)

        self._current_sleep["events_reviewed"] += reviewed
        self._current_sleep["links_reinforced"] += reinforced
        self._current_sleep["links_pruned"] += pruned
        self.last_consolidation = {
            "links_pruned": pruned,
            "links_reinforced": reinforced,
            "events_reviewed": reviewed,
        }

    def _finish_sleep_if_needed(self):
        if self.sleep_mode != "sleep":
            return

        self.sleep_ticks_remaining -= 1
        if self.sleep_ticks_remaining > 0:
            return

        self.sleep_mode = "awake"
        self.last_sleep_tick = self.tick_count
        summary = dict(self._current_sleep or {})
        summary.update({
            "end_tick": self.tick_count,
            "duration_ticks": self.tick_count - summary.get("start_tick", self.tick_count),
            "energy_after": round(self.energy_reserve, 4),
            "memory_pressure_after": round(self.memory_pressure, 4),
            "recent_event_count_after": len(self.recent_events),
        })
        self.last_sleep_summary = summary
        self._current_sleep = None
        self.quiet_ticks = 0

        if self.recent_events:
            keep = max(12, RECENT_EVENT_LIMIT // 4)
            self.recent_events = sorted(
                self.recent_events,
                key=lambda e: (e.get("significance", 0.0), e.get("tick", 0)),
                reverse=True,
            )[:keep]

    def step(self, senses=None, **kw):
        """
        Advance the field one tick on normalized sensor values (0-1).

        `senses` is a dict with keys sound, light, motion, weather. Missing
        senses default to 0.0. Keyword form also works: step(sound=.5, light=.2).
        """
        values = {s: 0.0 for s in SENSES}
        if senses:
            values.update({k: v for k, v in senses.items() if k in values})
        if kw:
            values.update({k: v for k, v in kw.items() if k in values})

        self.last_events = []
        self.last_sleep_summary = None
        self.last_consolidation = {
            "links_pruned": 0,
            "links_reinforced": 0,
            "events_reviewed": 0,
        }
        self._replenish_energy()

        cells = self.cells
        prev_activation = {n: cells[n].activation for n in cells}
        prev_ripple = {n: cells[n].ripple for n in cells}
        prev_velocity = {n: cells[n].ripple_velocity for n in cells}
        relevance_total = sum(max(0.05, c.relevance) for c in cells.values())
        body_tone = (
            sum(prev_activation[n] * max(0.05, cells[n].relevance) for n in cells)
            / relevance_total
        )

        deltas = {s: abs(values[s] - self.last_sense[s]) for s in SENSES}
        sensor_scale = SLEEP_SENSOR_SCALE if self.sleep_mode == "sleep" else 1.0
        sensed = {s: values[s] * sensor_scale for s in SENSES}

        neighbor_activation = {n: 0.0 for n in cells}
        neighbor_ripple = {n: 0.0 for n in cells}
        neighbor_ripple_weight = {n: 0.0 for n in cells}

        for (i, j), w in self.weights.items():
            if w <= 0.0:
                continue
            neighbor_activation[i] += prev_activation[j] * w
            neighbor_activation[j] += prev_activation[i] * w
            neighbor_ripple[i] += prev_ripple[j] * w
            neighbor_ripple[j] += prev_ripple[i] * w
            neighbor_ripple_weight[i] += w
            neighbor_ripple_weight[j] += w

        pressure = {}
        next_ripple = {}
        next_velocity = {}

        for n in cells:
            direct = sum(sensed[s] * self.sense_weight[s][n] for s in SENSES)

            incoming_wave = 0.0
            if neighbor_ripple_weight[n] > 0.0:
                incoming_wave = neighbor_ripple[n] / neighbor_ripple_weight[n]

            sensor_impulse = (
                sum(deltas[s] * self.sense_weight[s][n] for s in SENSES)
                * SENSOR_RIPPLE_GAIN * sensor_scale
            )
            for s in SENSES:
                if n == SENSE_ANCHOR[s]:
                    sensor_impulse += sensed[s] * ANCHOR_RIPPLE_DRIVE

            velocity = (
                prev_velocity[n] * RIPPLE_VELOCITY_DECAY
                + RIPPLE_SPREAD * (incoming_wave - prev_ripple[n])
                + sensor_impulse
            )
            ripple = clamp(prev_ripple[n] * RIPPLE_DECAY + velocity, -RIPPLE_CLAMP, RIPPLE_CLAMP)
            next_velocity[n] = velocity
            next_ripple[n] = ripple

            if abs(sensor_impulse) > 0.001:
                cells[n].last_impulse_tick = self.tick_count

            pressure[n] = (
                direct
                + NEIGHBOR_ACTIVATION_GAIN * min(neighbor_activation[n], NEIGHBOR_DRIVE_CLAMP)
                + RIPPLE_PRESSURE_GAIN * max(0.0, ripple)
            )
            if cells[n].type == "anchor_emitter":
                pressure[n] += EMITTER_BODY_PRESSURE_GAIN * body_tone

        for n in cells:
            cell = cells[n]
            cell.ripple = next_ripple[n]
            cell.ripple_velocity = next_velocity[n]
            cell.pressure = pressure[n]
            cell.age += 1

            if self._should_update_cell(n, pressure[n], next_ripple[n]):
                cost = (
                    BASE_UPDATE_COST
                    + ACTIVATION_COST * pressure[n]
                    + RIPPLE_COST * abs(next_ripple[n])
                )
                energy_scale = self._spend_cell_energy(cell, cost)
                if cell.energy <= CELL_ENERGY_FLOOR_TO_FIRE and pressure[n] < ACTIVE_PRESSURE_THRESHOLD:
                    energy_scale *= 0.35

                ripple_drive = RIPPLE_TO_ACTIVATION * max(0.0, next_ripple[n]) * energy_scale
                noise = NOISE_FLOOR * random.random() * energy_scale

                if CELL_MODEL == "predictive":
                    # The cell predicts its own drive and emits the surprise.
                    # No shared homeostatic target, so nothing equalizes cells.
                    pred_error = pressure[n] - cell.prediction
                    cell.surprise = abs(pred_error)
                    input_drive = (1.0 - DECAY_RATE) * PRED_GAIN * cell.surprise * energy_scale
                    activation = prev_activation[n] * DECAY_RATE + input_drive + ripple_drive + noise
                    cell.activation = clamp(activation, 0.0, 1.0)
                    cell.prediction = clamp(
                        cell.prediction + PRED_BETA * pred_error * energy_scale, 0.0, PRED_MAX
                    )
                    cell.avg_activation += (cell.activation - cell.avg_activation) * HOMEO_AVG_RATE
                else:
                    input_drive = (1.0 - DECAY_RATE) * cell.homeo_gain * pressure[n] * energy_scale
                    activation = prev_activation[n] * DECAY_RATE + input_drive + ripple_drive + noise
                    cell.activation = clamp(activation, 0.0, 1.0)
                    if HOMEO_ENABLED:
                        cell.avg_activation += (cell.activation - cell.avg_activation) * HOMEO_AVG_RATE
                        error = cell.avg_activation - HOMEO_TARGET
                        cell.homeo_gain = clamp(
                            cell.homeo_gain - error * HOMEO_RATE,
                            HOMEO_GAIN_MIN,
                            HOMEO_GAIN_MAX,
                        )

                cell.last_tick = self.tick_count

                fatigue_gain = (
                    cell.activation * FATIGUE_GAIN
                    + pressure[n] * FATIGUE_PRESSURE_GAIN
                ) * energy_scale
                cell.fatigue = clamp(
                    cell.fatigue + fatigue_gain - FATIGUE_RECOVERY * (1.0 - cell.activation),
                    0.0,
                    1.0,
                )
            else:
                decay = self._activation_decay_for(cell)
                cell.activation = clamp(prev_activation[n] * decay, 0.0, 1.0)
                recovery = FATIGUE_SLEEP_RECOVERY if cell.state in ("dormant", "deep_sleep") else FATIGUE_RECOVERY
                cell.fatigue = clamp(cell.fatigue - recovery, 0.0, 1.0)

            self._set_cell_state(cell)

        for a in EMITTER_ANCHORS:
            emitter = cells[a]
            if emitter.state == "active":
                self._spend_cell_energy(emitter, emitter.activation * EMITTER_COST)

        # The reservoir reads the freshly-updated in-between cells and advances
        # its echo. It does not feed back into the ring yet (the trained readout
        # to the emitters is step 3), so it is a read-only observer for now.
        if RESERVOIR_ENABLED:
            self.reservoir_input = [cells[n].activation for n in IN_BETWEEN_CELLS]
            self.reservoir_state = self.reservoir_step(self.reservoir_state, self.reservoir_input)
            if READOUT_ENABLED:
                self._update_readout(values)

        if HEBBIAN_ENABLED:
            self._hebbian()

        self._record_significant_event(values, deltas)
        self._update_relevance()
        self._update_memory_pressure()
        self._maybe_enter_sleep(deltas)
        if self.sleep_mode == "sleep":
            self._sleep_maintenance()
            self._update_memory_pressure()
            self._finish_sleep_if_needed()

        self.tick_count += 1
        self.last_sense = dict(values)
        return self.state()

    def _hebbian(self):
        cells = self.cells
        for key, w in list(self.weights.items()):
            i, j = key
            self.connection_age[key] += 1

            ci, cj = cells[i], cells[j]
            coactivity = ci.activation * cj.activation
            pair_pressure = (ci.pressure + cj.pressure) * 0.5
            assoc = self.connection_pressure.get(key, 0.0) * PRESSURE_ASSOC_DECAY

            if coactivity >= COACTIVITY_THRESHOLD and pair_pressure >= PRESSURE_LEARNING_THRESHOLD:
                energy_scale = min(
                    self._spend_cell_energy(ci, LEARNING_COST * 0.5),
                    self._spend_cell_energy(cj, LEARNING_COST * 0.5),
                    self._spend_reserve(LEARNING_COST),
                )
                if energy_scale > 0.0:
                    w += (
                        ETA
                        * coactivity
                        * (1.0 + PRESSURE_LEARNING_GAIN * pair_pressure)
                        * energy_scale
                    )
                    self.connection_usage[key] += 1
                    self.connection_last_active_tick[key] = self.tick_count
                    assoc += (pair_pressure - assoc) * PRESSURE_ASSOC_GAIN

            usage = self.connection_usage[key]
            last_active = self.connection_last_active_tick[key]
            inactive_for = self.tick_count - last_active if last_active >= 0 else self.tick_count
            recency = 1.0 if inactive_for < PRUNE_AFTER_INACTIVE_TICKS else 0.0
            stability = (
                0.50 * min(1.0, usage / 120.0)
                + 0.35 * clamp(assoc, 0.0, 1.0)
                + 0.15 * recency
            )
            unprotected = 1.0 - clamp(stability, 0.0, 1.0)
            decay = STRUCTURAL_DECAY + UNUSED_DECAY * unprotected * unprotected
            if self.sleep_mode == "sleep":
                decay *= 1.5
            w = clamp(w, 0.0, W_MAX)
            w *= (1.0 - decay)

            self.weights[key] = max(w, PRUNE_FLOOR)
            self.connection_pressure[key] = clamp(assoc, 0.0, 1.0)

    def state(self):
        """Full live snapshot for the dashboard and logging."""
        return {
            "version": SNAPSHOT_VERSION,
            "field_version": FIELD_VERSION,
            "tick": self.tick_count,
            "cell_count": CELL_COUNT,
            "ring": [label for label, _t, _h in RING],
            "sense_anchors": SENSE_ANCHOR,
            "emitter_anchors": EMITTER_ANCHORS,
            "state_counts": self._state_counts(),
            "metabolism": self._metabolism_summary(),
            "reservoir": {
                "size": RESERVOIR_SIZE,
                "spectral_radius": self.reservoir_radius,
                "state": [round(x, 4) for x in self.reservoir_state],
                "input": [round(x, 4) for x in self.reservoir_input],
            },
            "readout": {
                hw: {
                    "predicts": READOUT_TARGET[hw],
                    "output": round(self.readout_output[hw], 4),
                    "weights": [round(w, 4) for w in self.readout_w[hw]],
                }
                for hw in READOUT_TARGET
            },
            "emitter_activation": round(self.emitter_activation, 4),
            "emitter_activations": self.emitter_activations(),
            "events": self.last_events,
            "recent_events": self.recent_events[-12:],
            "sleep_summary": self.last_sleep_summary,
            "cells": [
                {
                    "n": c.n,
                    "name": c.name,
                    "label": c.label,
                    "type": c.type,
                    "hardware_id": c.hardware_id,
                    "activation": round(c.activation, 4),
                    "pressure": round(c.pressure, 4),
                    "energy": round(c.energy, 4),
                    "fatigue": round(c.fatigue, 4),
                    "relevance": round(c.relevance, 4),
                    "ripple": round(c.ripple, 4),
                    "ripple_velocity": round(c.ripple_velocity, 4),
                    "state": c.state,
                    "tick_interval": c.tick_interval,
                    "last_active_tick": c.last_active_tick,
                    "last_impulse_tick": c.last_impulse_tick,
                    "last_tick": c.last_tick,
                    "homeo_gain": round(c.homeo_gain, 4),
                }
                for c in self.cells.values()
            ],
            "connections": [
                {
                    "a": i,
                    "b": j,
                    "weight": round(w, 6),
                    "age": self.connection_age.get((i, j), 0),
                    "usage_count": self.connection_usage.get((i, j), 0),
                    "pressure_association": round(self.connection_pressure.get((i, j), 0.0), 6),
                    "last_active_tick": self.connection_last_active_tick.get((i, j), -1),
                }
                for (i, j), w in self.weights.items()
            ],
        }

    def snapshot(self):
        """Only persistent structure and slow operational state."""
        return {
            "version": SNAPSHOT_VERSION,
            "field_version": FIELD_VERSION,
            "saved_at": datetime.now().isoformat(),
            "tick": self.tick_count,
            "cell_count": CELL_COUNT,
            "energy_reserve": round(self.energy_reserve, 6),
            "memory_pressure": round(self.memory_pressure, 6),
            "sleep_mode": self.sleep_mode,
            "last_sleep_tick": self.last_sleep_tick,
            "recent_events": self.recent_events[-RECENT_EVENT_LIMIT:],
            "cells": {
                str(c.n): {
                    "energy": round(c.energy, 6),
                    "fatigue": round(c.fatigue, 6),
                    "relevance": round(c.relevance, 6),
                    "size": round(c.size, 6),
                    "age": c.age,
                    "health": round(c.health, 6),
                    "avg_activation": round(c.avg_activation, 6),
                    "homeo_gain": round(c.homeo_gain, 6),
                    "state": c.state,
                    "activation_threshold": round(c.activation_threshold, 6),
                    "last_active_tick": c.last_active_tick,
                    "last_impulse_tick": c.last_impulse_tick,
                    "last_tick": c.last_tick,
                    "tick_interval": c.tick_interval,
                    "wake_sensitivity": round(c.wake_sensitivity, 6),
                }
                for c in self.cells.values()
            },
            "connections": [
                {
                    "a": i,
                    "b": j,
                    "weight": round(w, 6),
                    "age": self.connection_age.get((i, j), 0),
                    "usage_count": self.connection_usage.get((i, j), 0),
                    "pressure_association": round(self.connection_pressure.get((i, j), 0.0), 6),
                    "last_active_tick": self.connection_last_active_tick.get((i, j), -1),
                }
                for (i, j), w in self.weights.items()
            ],
        }

    def restore(self, data):
        """Apply a snapshot back onto the current field. Returns True if applied."""
        snapshot_cells = data.get("cell_count")
        version = data.get("version")
        if snapshot_cells != CELL_COUNT:
            print(f"Field snapshot refused: cell_count {snapshot_cells} "
                  f"does not match current field ({CELL_COUNT}). Starting fresh.")
            return False
        if not isinstance(version, int) or version > SNAPSHOT_VERSION:
            print(f"Field snapshot refused: version {version!r} is newer than "
                  f"this code supports ({SNAPSHOT_VERSION}). Starting fresh.")
            return False

        self.energy_reserve = data.get("energy_reserve", self.energy_reserve)
        self.memory_pressure = data.get("memory_pressure", self.memory_pressure)
        self.sleep_mode = data.get("sleep_mode", "awake")
        self.last_sleep_tick = data.get("last_sleep_tick", self.last_sleep_tick)
        self.recent_events = data.get("recent_events", [])[-RECENT_EVENT_LIMIT:]

        for key, cell_data in data.get("cells", {}).items():
            try:
                n = int(key)
            except (TypeError, ValueError):
                continue
            if n not in self.cells:
                continue
            cell = self.cells[n]
            for attr in (
                "energy", "fatigue", "relevance", "size", "age", "health",
                "avg_activation", "homeo_gain", "activation_threshold",
                "last_active_tick", "last_impulse_tick", "last_tick",
                "tick_interval", "wake_sensitivity",
            ):
                if attr in cell_data:
                    setattr(cell, attr, cell_data[attr])
            if "state" in cell_data:
                cell.state = map_legacy_state(cell_data["state"])

        for conn in data.get("connections", []):
            try:
                i, j = conn["a"], conn["b"]
            except KeyError:
                continue
            key = (i, j) if i < j else (j, i)
            if key not in self.weights:
                continue
            self.weights[key] = max(PRUNE_FLOOR, conn.get("weight", self.weights[key]))
            self.connection_age[key] = conn.get("age", self.connection_age.get(key, 0))
            self.connection_usage[key] = conn.get("usage_count", self.connection_usage.get(key, 0))
            self.connection_pressure[key] = conn.get(
                "pressure_association", self.connection_pressure.get(key, 0.0)
            )
            self.connection_last_active_tick[key] = conn.get(
                "last_active_tick", self.connection_last_active_tick.get(key, -1)
            )

        self.tick_count = data.get("tick", 0)
        return True


def build_field():
    return CellField()


def save_field(field, path):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as state_file:
        json.dump(field.snapshot(), state_file, indent=2)
    os.replace(tmp_path, path)


def load_field(field, path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as state_file:
            data = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return None
    if not field.restore(data):
        return None
    return data


if __name__ == "__main__":
    random.seed(0)
    field = build_field()
    print(f"Ring: {CELL_COUNT} cells")
    for n, (label, t, h) in enumerate(RING):
        print(f"  C{n:<2} {label:<18} {t:<14} {h or ''}")
    print(f"Sense anchors: {SENSE_ANCHOR}")
    print(f"Emitter anchors: {EMITTER_ANCHORS}")
    print(f"Ring edges: {len(field.weights)}")
    print(f"In-between cells driving reservoir: {IN_BETWEEN_CELLS}")
    print(f"Reservoir: {RESERVOIR_SIZE} cells, target radius "
          f"{RESERVOIR_SPECTRAL_RADIUS}, measured {field.reservoir_radius}")
