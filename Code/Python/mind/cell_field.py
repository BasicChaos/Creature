"""
Cell field (v05.4 - structure matters).

v05.4 keeps the 111-cell body and v05.3's scarcity, and makes the learned
structure actually shape behavior:

* neighbor drive is a weighted SUM, not a weighted average: stronger
  pathways now deliver more signal, so Hebbian growth changes routing AND
  gain instead of only relative routing
* the per-cell connection budget is gone; homeostatic gain is the one
  stabilizer that counters runaway drive
* pruning is demotion, not amputation: weights floor at a small scar value
  (PRUNE_FLOOR) instead of cutting to zero. Scarred links carry almost no
  learned drive, but ripples still pass through scarred tissue, so a strong
  event can recolonize dark regions. Hard-zero pruning made disconnection
  permanent: cut-off cells could never co-fire again.
* structural decay is slowed so the steady state is a shaped field, not an
  emptying one

The collector steps the field once per second. All constants assume that 1 Hz
field tick. Judge any tuning change with tools/field_lab.py, control vs
variant, before deploying it.
"""

import json
import os
import random
from datetime import datetime

CELL_COUNT = 111
SNAPSHOT_VERSION = 5
FIELD_VERSION = "v05.4.1"

# Stable cell roles. IDs are persistent history, not fixed physical positions.
SOUND_ANCHOR = 5      # INMP441
EMITTER_ANCHOR = 29   # onboard NeoPixel, centered in the larger field
LIGHT_ANCHOR = 33     # BH1750

# Organic row layout, centered in a 13-column coordinate space.
# Sum = 111. IDs are assigned in reading order, then the historical anchor IDs
# are swapped into their body positions.
_ROW_SPECS = [
    (7, 3),
    (9, 2),
    (11, 1),
    (11, 1),
    (11, 1),
    (13, 0),
    (11, 1),
    (11, 1),
    (11, 1),
    (9, 2),
    (7, 3),
]

_SOUND_POSITION = (5, 0)
_EMITTER_POSITION = (5, 6)
_LIGHT_POSITION = (5, 12)


def _build_positions():
    positions = []
    for row, (length, col_start) in enumerate(_ROW_SPECS):
        for i in range(length):
            positions.append((row, col_start + i))
    return positions


def _build_coords():
    positions = _build_positions()
    if len(positions) != CELL_COUNT:
        raise ValueError(f"Expected {CELL_COUNT} positions, got {len(positions)}")

    coords = {n: positions[n - 1] for n in range(1, CELL_COUNT + 1)}

    def swap_cell_to(cell_id, target):
        occupant = next(n for n, pos in coords.items() if pos == target)
        coords[occupant], coords[cell_id] = coords[cell_id], coords[occupant]

    swap_cell_to(SOUND_ANCHOR, _SOUND_POSITION)
    swap_cell_to(EMITTER_ANCHOR, _EMITTER_POSITION)
    swap_cell_to(LIGHT_ANCHOR, _LIGHT_POSITION)
    return coords


COORDS = _build_coords()  # cell number -> (row, col)
MAX_ROW = max(row for row, _ in COORDS.values())
MAX_COL = max(col for _, col in COORDS.values())

# Activation math.
DECAY_RATE = 0.90
RESTING_ACTIVATION_DECAY = 0.965
DORMANT_ACTIVATION_DECAY = 0.985
DEEP_SLEEP_ACTIVATION_DECAY = 0.992
NOISE_FLOOR = 0.0007

# Ripple math. Ripple is intentionally separate from activation: activation is
# local state, ripple is the passing wave that makes the field visibly respond.
RIPPLE_DECAY = 0.82
RIPPLE_VELOCITY_DECAY = 0.58
RIPPLE_SPREAD = 0.42
SENSOR_RIPPLE_GAIN = 0.65
SLEEP_SENSOR_SCALE = 0.35
ANCHOR_RIPPLE_DRIVE = 0.035
RIPPLE_PRESSURE_GAIN = 0.55
RIPPLE_TO_ACTIVATION = 0.025
RIPPLE_CLAMP = 1.25

# Wake and dormancy.
ACTIVE_ACTIVATION_THRESHOLD = 0.06
ACTIVE_RIPPLE_THRESHOLD = 0.08
ACTIVE_PRESSURE_THRESHOLD = 0.09
WAKE_PRESSURE_THRESHOLD = 0.065
WAKE_RIPPLE_THRESHOLD = 0.045
DORMANT_AFTER_TICKS = 75
DEEP_SLEEP_AFTER_TICKS = 300
EXPLORATION_WAKE_CHANCE = 0.0007
DEEP_SLEEP_WAKE_CHANCE = 0.00008

# Metabolism. Cells draw from a finite shared reserve, then spend their own
# local energy to sense/process/learn. Quiet cells refill slowly; active cells
# burn through energy and accumulate fatigue.
CELL_ENERGY_INIT = 0.55
CELL_ENERGY_MAX = 1.0
CELL_ENERGY_FLOOR_TO_FIRE = 0.035
GLOBAL_ENERGY_INIT = 24.0
GLOBAL_ENERGY_MAX = 36.0
# v05.4.1: raised 0.32 -> 0.7. At 0.32 the shared reserve could not cover the
# aggregate draw of ~20+ cells kept active by sustained input, so once it hit
# zero it deadlocked there: cells could not refill, yet kept firing on incoming
# pressure, and the field fell into chronic low-energy sleep (which applies the
# 1.5x structural-decay penalty and erodes connections). Replaying the real
# 2026-06-12 overnight log through field_lab confirmed it: at 0.32 the reserve
# drained to 0 by tick ~3000 and live links collapsed 386 -> 30; at 0.7 the
# reserve holds near 35, low-energy sleeps drop 45 -> 2, and ~2.5x more links
# survive. Re-test with tools/field_lab.py if you change this.
GLOBAL_REPLENISH_PER_TICK = 0.7
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

# Structural memory and connection competition.
# v05.4: decay rebalanced so the steady state is a shaped field. Three
# timescales now separate cleanly:
#   fresh unused links   -> sink to the scar floor within hours (scarcity)
#   occasionally used    -> hold while rehearsed, fade over a day of quiet
#   well-consolidated    -> survive ~a day of total silence; usage never
#                           expires, association fades over ~10 h, and sleep
#                           replay re-touches whatever stays significant
# v05.3 had PRESSURE_ASSOC_DECAY=0.9992: pathway importance evaporated with a
# ~15 minute half-life, so no structure could defend itself through a quiet
# afternoon, let alone a weekend.
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
# Scar floor: decayed/pruned links rest here instead of at zero. Below
# LIVE_LINK_THRESHOLD a link counts as scarred (dashboard ghost, stats).
PRUNE_FLOOR = 0.02
LIVE_LINK_THRESHOLD = 0.05
PRUNE_AFTER_INACTIVE_TICKS = 3600
CONSOLIDATION_UNUSED_KEEP = 0.995
CONSOLIDATION_WEAK_KEEP = 0.998

# Sleep and replay.
LOW_STIMULATION_TICKS = 160
SLEEP_DURATION_TICKS = 30
SLEEP_COOLDOWN_TICKS = 240
MEMORY_PRESSURE_SLEEP_THRESHOLD = 0.58
LOW_RESERVE_SLEEP_THRESHOLD = 7.5
RECENT_EVENT_LIMIT = 80
SIGNIFICANT_PRESSURE_THRESHOLD = 0.18
SIGNIFICANT_DELTA_THRESHOLD = 0.16
REPLAY_EVENTS_PER_TICK = 2

# Homeostatic layer. With the budget gone this is the load-bearing
# stabilizer against runaway drive. v05.3's target of 0.12 was unreachable
# (the field settled near 0.06 and many gains railed at max, which means the
# regulator had stopped regulating).
HOMEO_ENABLED = True
HOMEO_TARGET = 0.06
HOMEO_AVG_RATE = 0.01
HOMEO_RATE = 0.018
HOMEO_GAIN_INIT = 0.22
HOMEO_GAIN_MIN = 0.0
HOMEO_GAIN_MAX = 4.0

# Sensor influence is deliberately local. The visible long-range effect should
# come from ripples and connections, not from every cell receiving direct input.
SENSOR_WEIGHT_BY_DISTANCE = {
    0: 1.00,
    1: 0.52,
    2: 0.24,
    3: 0.10,
    4: 0.04,
}

# Local topology only. Radius 1 is the immediate Moore neighborhood on the
# row/column coordinates. Decayed links rest at PRUNE_FLOOR (a scar, not a
# cut) and can regrow when a strong event drives both ends again.
#
# v05.4: neighbor drive is a clamped weighted SUM. With ~5-8 links per cell
# at initial weight 0.20 the starting sum matches the old averaged drive at
# gain 0.75, so the field wakes up behaving familiarly - but now strengthened
# pathways deliver more signal instead of being normalized away.
CONNECTION_RADIUS = 1
CONNECTION_WEIGHT_BY_DISTANCE = {1: 0.20}
NEIGHBOR_ACTIVATION_GAIN = 0.45
NEIGHBOR_DRIVE_CLAMP = 1.5
EMITTER_BODY_PRESSURE_GAIN = 1.15


def clamp(value, low, high):
    return max(low, min(high, value))


def grid_distance(i, j):
    """Chebyshev distance between two cells on the 2D tissue grid."""
    (r1, c1), (r2, c2) = COORDS[i], COORDS[j]
    return max(abs(r1 - r2), abs(c1 - c2))


def sensor_weight(distance):
    return SENSOR_WEIGHT_BY_DISTANCE.get(distance, 0.0)


def map_legacy_state(state):
    """Map old v05.2 sleep labels onto the v05.3 state names."""
    return {
        "active": "active",
        "light_sleep": "resting",
        "resting": "resting",
        "dormant": "dormant",
        "deep_sleep": "deep_sleep",
    }.get(state, "resting")


class FieldCell:
    """One cell. Long-term memory is derived from links, not stored here."""

    def __init__(self, n, cell_type, hardware_id=None):
        self.n = n
        self.name = f"C{n}"
        self.type = cell_type
        self.hardware_id = hardware_id
        self.row, self.col = COORDS[n]

        # Fast operational state.
        self.activation = 0.0
        self.pressure = 0.0
        self.ripple = 0.0
        self.ripple_velocity = 0.0

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

        # Developmental flags. Used later for growth, movement, mitosis, death.
        self.can_move = cell_type == "free"
        self.can_divide = True
        self.can_die = cell_type == "free"

    def _default_tick_interval(self):
        if self.hardware_id:
            return 1
        d_emitter = grid_distance(self.n, EMITTER_ANCHOR)
        d_sound = grid_distance(self.n, SOUND_ANCHOR)
        d_light = grid_distance(self.n, LIGHT_ANCHOR)
        d_anchor = min(d_emitter, d_sound, d_light)
        if d_anchor <= 2:
            return 2
        if d_anchor <= 4:
            return 4
        if d_anchor <= 6:
            return 8 + (self.n % 3)
        return 18 + (self.n % 7)


class CellField:
    """The 111-cell field: v05.3 scarcity plus v05.4 structure-that-matters."""

    def __init__(self):
        self.cells = {}
        self.weights = {}
        self.connection_age = {}
        self.connection_usage = {}
        self.connection_pressure = {}
        self.connection_last_active_tick = {}
        self.sound_weight = {}
        self.light_weight = {}
        self.tick_count = 0
        self.last_sound_value = 0.0
        self.last_light_value = 0.0

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
        self._build()

    def _build(self):
        for n in range(1, CELL_COUNT + 1):
            if n == SOUND_ANCHOR:
                self.cells[n] = FieldCell(n, "anchor_sensor", "sound")
            elif n == LIGHT_ANCHOR:
                self.cells[n] = FieldCell(n, "anchor_sensor", "light")
            elif n == EMITTER_ANCHOR:
                self.cells[n] = FieldCell(n, "anchor_emitter", "emitter_main")
            else:
                self.cells[n] = FieldCell(n, "free")

        for n in range(1, CELL_COUNT + 1):
            self.sound_weight[n] = sensor_weight(grid_distance(n, SOUND_ANCHOR))
            self.light_weight[n] = sensor_weight(grid_distance(n, LIGHT_ANCHOR))

        for i in range(1, CELL_COUNT + 1):
            for j in range(i + 1, CELL_COUNT + 1):
                d = grid_distance(i, j)
                if d <= CONNECTION_RADIUS:
                    key = (i, j)
                    self.weights[key] = CONNECTION_WEIGHT_BY_DISTANCE[d]
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
        return self.cells[EMITTER_ANCHOR].activation

    def _state_counts(self):
        counts = {"active": 0, "resting": 0, "dormant": 0, "deep_sleep": 0}
        for cell in self.cells.values():
            counts[cell.state] = counts.get(cell.state, 0) + 1
        return counts

    def _metabolism_summary(self):
        cells = list(self.cells.values())
        # "Live" means carrying meaningful learned weight; links at or near
        # the scar floor are reported as pruned (the dashboard draws them as
        # ghosts), even though they still pass ripples.
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
        # Passive pressure/ripple are computed for every cell above. Energy no
        # longer gates eligibility: a poor cell still acts, just weakly, via
        # energy_scale in _spend_cell_energy. Only fatigue blocks active work.
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

        if (
            active_signal
            and cell.fatigue < FATIGUE_SLEEP_THRESHOLD
        ):
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

    def _record_significant_event(self, sound_value, light_value, sound_delta, light_delta):
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
            and max(sound_delta, light_delta) < SIGNIFICANT_DELTA_THRESHOLD
        ):
            return

        event = {
            "tick": self.tick_count,
            "type": "pressure_spike",
            "significance": round(significance, 4),
            "sound_norm": round(sound_value, 4),
            "light_norm": round(light_value, 4),
            "sound_delta": round(sound_delta, 4),
            "light_delta": round(light_delta, 4),
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

    def _maybe_enter_sleep(self, sound_delta, light_delta):
        if self.sleep_mode == "sleep":
            return
        if self.tick_count - self.last_sleep_tick < SLEEP_COOLDOWN_TICKS:
            return

        sensor_motion = max(sound_delta, light_delta)
        max_pressure = max(c.pressure for c in self.cells.values())
        if sensor_motion < 0.015 and max_pressure < 0.055:
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

        # Consume replayed events so each experience is reinforced once per
        # sleep. Without this, the same top events are replayed every sleep
        # tick and a single spike can be reinforced ~30x in one sleep.
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
            # Consolidation demotes unprotected weak links to the scar floor.
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

    def step(self, sound_value, light_value):
        """
        Advance the field one tick on normalized sensor values (0-1).

        Pressure and ripple are computed from previous state, then cells spend
        energy to update. Connections become the persistent memory layer.
        """
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

        sound_delta = abs(sound_value - self.last_sound_value)
        light_delta = abs(light_value - self.last_light_value)
        sensor_scale = SLEEP_SENSOR_SCALE if self.sleep_mode == "sleep" else 1.0
        sensed_sound = sound_value * sensor_scale
        sensed_light = light_value * sensor_scale

        # Activation flows as a clamped weighted sum (learned pathways carry
        # more). Ripple stays a weighted average: it is the tissue's wave
        # medium, and the average lets waves cross scarred regions, which is
        # how dark tissue gets a chance to regrow.
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
            s = sensed_sound * self.sound_weight[n]
            l = sensed_light * self.light_weight[n]

            incoming_wave = 0.0
            if neighbor_ripple_weight[n] > 0.0:
                incoming_wave = neighbor_ripple[n] / neighbor_ripple_weight[n]

            sensor_impulse = (
                sound_delta * self.sound_weight[n]
                + light_delta * self.light_weight[n]
            ) * SENSOR_RIPPLE_GAIN * sensor_scale
            if n == SOUND_ANCHOR:
                sensor_impulse += sensed_sound * ANCHOR_RIPPLE_DRIVE
            elif n == LIGHT_ANCHOR:
                sensor_impulse += sensed_light * ANCHOR_RIPPLE_DRIVE

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
                s
                + l
                + NEIGHBOR_ACTIVATION_GAIN * min(neighbor_activation[n], NEIGHBOR_DRIVE_CLAMP)
                + RIPPLE_PRESSURE_GAIN * max(0.0, ripple)
            )
            if n == EMITTER_ANCHOR:
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

                input_drive = (1.0 - DECAY_RATE) * cell.homeo_gain * pressure[n] * energy_scale
                ripple_drive = RIPPLE_TO_ACTIVATION * max(0.0, next_ripple[n]) * energy_scale
                noise = NOISE_FLOOR * random.random() * energy_scale
                activation = prev_activation[n] * DECAY_RATE + input_drive + ripple_drive + noise
                cell.activation = clamp(activation, 0.0, 1.0)
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

                if HOMEO_ENABLED:
                    cell.avg_activation += (cell.activation - cell.avg_activation) * HOMEO_AVG_RATE
                    error = cell.avg_activation - HOMEO_TARGET
                    cell.homeo_gain = clamp(
                        cell.homeo_gain - error * HOMEO_RATE,
                        HOMEO_GAIN_MIN,
                        HOMEO_GAIN_MAX,
                    )
            else:
                decay = self._activation_decay_for(cell)
                cell.activation = clamp(prev_activation[n] * decay, 0.0, 1.0)
                recovery = FATIGUE_SLEEP_RECOVERY if cell.state in ("dormant", "deep_sleep") else FATIGUE_RECOVERY
                cell.fatigue = clamp(cell.fatigue - recovery, 0.0, 1.0)

            self._set_cell_state(cell)

        emitter = cells[EMITTER_ANCHOR]
        if emitter.state == "active":
            self._spend_cell_energy(emitter, emitter.activation * EMITTER_COST)

        if HEBBIAN_ENABLED:
            self._hebbian()

        self._record_significant_event(sound_value, light_value, sound_delta, light_delta)
        self._update_relevance()
        self._update_memory_pressure()
        self._maybe_enter_sleep(sound_delta, light_delta)
        if self.sleep_mode == "sleep":
            self._sleep_maintenance()
            self._update_memory_pressure()
            self._finish_sleep_if_needed()

        self.tick_count += 1
        self.last_sound_value = sound_value
        self.last_light_value = light_value
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
            # Quadratic stability law. Usage is weighted highest because it
            # never expires: a pathway that fired 120+ times keeps half its
            # protection forever. The square lets consolidation span two
            # orders of magnitude of decay rate, which a linear law cannot:
            # fresh links sink in hours while consolidated ones hold for a
            # day of pure silence. (v05.3 capped stability at 0.85, so even
            # the best pathway lost ~3%/h of quiet.)
            unprotected = 1.0 - clamp(stability, 0.0, 1.0)
            decay = STRUCTURAL_DECAY + UNUSED_DECAY * unprotected * unprotected
            if self.sleep_mode == "sleep":
                decay *= 1.5
            w = clamp(w, 0.0, W_MAX)
            w *= (1.0 - decay)

            # Scar floor: unused links decay down to a vestigial weight, never
            # to zero. They carry almost no learned drive but stay part of the
            # tissue, so activity can reach them again.
            self.weights[key] = max(w, PRUNE_FLOOR)
            self.connection_pressure[key] = clamp(assoc, 0.0, 1.0)

    def state(self):
        """Full live snapshot for the dashboard and logging."""
        return {
            "version": SNAPSHOT_VERSION,
            "field_version": FIELD_VERSION,
            "tick": self.tick_count,
            "cell_count": CELL_COUNT,
            "anchors": {
                "sound": SOUND_ANCHOR,
                "emitter": EMITTER_ANCHOR,
                "light": LIGHT_ANCHOR,
            },
            "layout": {
                "row_specs": _ROW_SPECS,
                "max_row": MAX_ROW,
                "max_col": MAX_COL,
            },
            "state_counts": self._state_counts(),
            "sleep_counts": self._state_counts(),
            "metabolism": self._metabolism_summary(),
            "emitter_activation": round(self.emitter_activation, 4),
            "events": self.last_events,
            "recent_events": self.recent_events[-12:],
            "sleep_summary": self.last_sleep_summary,
            "cells": [
                {
                    "n": c.n,
                    "name": c.name,
                    "type": c.type,
                    "hardware_id": c.hardware_id,
                    "row": c.row,
                    "col": c.col,
                    "activation": round(c.activation, 4),
                    "pressure": round(c.pressure, 4),
                    "energy": round(c.energy, 4),
                    "fatigue": round(c.fatigue, 4),
                    "relevance": round(c.relevance, 4),
                    "ripple": round(c.ripple, 4),
                    "ripple_velocity": round(c.ripple_velocity, 4),
                    "state": c.state,
                    "sleep_state": c.state,
                    "tick_interval": c.tick_interval,
                    "last_active_tick": c.last_active_tick,
                    "last_impulse_tick": c.last_impulse_tick,
                    "last_tick": c.last_tick,
                    "size": round(c.size, 4),
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
            "anchors": {
                "sound": SOUND_ANCHOR,
                "emitter": EMITTER_ANCHOR,
                "light": LIGHT_ANCHOR,
            },
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
        """Apply a snapshot back onto the current field. Returns True if applied.

        v05.2 snapshots are accepted: old per-cell memory is treated as a weak
        relevance hint, and old sleep names are mapped onto v05.3 states.

        A snapshot whose shape does not match the current field (different
        cell count, or a newer snapshot format) is refused entirely rather
        than half-applied. The field then starts fresh.
        """
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
                "energy",
                "fatigue",
                "relevance",
                "size",
                "age",
                "health",
                "avg_activation",
                "homeo_gain",
                "activation_threshold",
                "last_active_tick",
                "last_impulse_tick",
                "last_tick",
                "tick_interval",
                "wake_sensitivity",
            ):
                if attr in cell_data:
                    setattr(cell, attr, cell_data[attr])
            if "state" in cell_data:
                cell.state = map_legacy_state(cell_data["state"])
            elif "sleep_state" in cell_data:
                cell.state = map_legacy_state(cell_data["sleep_state"])
            elif "memory" in cell_data:
                cell.relevance = clamp(float(cell_data["memory"]) * 2.5, 0.0, 1.0)

        for conn in data.get("connections", []):
            try:
                i, j = conn["a"], conn["b"]
            except KeyError:
                continue
            key = (i, j) if i < j else (j, i)
            if key not in self.weights:
                continue
            # v05.3 snapshots may carry hard-zero pruned links; migrate them
            # up to the scar floor so the tissue stays fully connected.
            self.weights[key] = max(
                PRUNE_FLOOR, conn.get("weight", self.weights[key])
            )
            self.connection_age[key] = conn.get("age", self.connection_age.get(key, 0))
            self.connection_usage[key] = conn.get("usage_count", self.connection_usage.get(key, 0))
            legacy_pressure = min(0.2, self.connection_usage[key] / 600.0)
            self.connection_pressure[key] = conn.get(
                "pressure_association",
                self.connection_pressure.get(key, legacy_pressure),
            )
            self.connection_last_active_tick[key] = conn.get(
                "last_active_tick",
                self.connection_last_active_tick.get(key, -1),
            )

        self.tick_count = data.get("tick", 0)
        return True


def build_field():
    return CellField()


def save_field(field, path):
    """Atomic write of the field's slow state (temp file + rename)."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as state_file:
        json.dump(field.snapshot(), state_file, indent=2)
    os.replace(tmp_path, path)


def load_field(field, path):
    """Load slow state if a usable snapshot exists. Returns the dict or None."""
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

    print(f"Grid: {CELL_COUNT} cells, sound=C{SOUND_ANCHOR}, "
          f"emitter=C{EMITTER_ANCHOR}, light=C{LIGHT_ANCHOR}")
    print(f"Anchor coordinates: sound={COORDS[SOUND_ANCHOR]}, "
          f"emitter={COORDS[EMITTER_ANCHOR]}, light={COORDS[LIGHT_ANCHOR]}")
    print(f"Connections wired (radius {CONNECTION_RADIUS}): {len(field.weights)} "
          f"of {CELL_COUNT * (CELL_COUNT - 1) // 2} possible pairs")

    print("\nRunning 1200 ticks with alternating impulses.")
    print("tick sound light  C29   maxAct active/rest/dorm/deep energy memP links")
    for t in range(1200):
        sound = 0.75 if t in (20, 21, 120, 121, 400, 401) else 0.04
        light = 0.8 if t in (250, 251, 520, 521, 522) else 0.03
        state = field.step(sound, light)
        if t % 100 == 0 or t == 1199:
            cells = field.cells
            max_act = max(c.activation for c in cells.values())
            counts = state["state_counts"]
            live_links = state["metabolism"]["live_connections"]
            total_links = state["metabolism"]["total_connections"]
            print(f"{t:4d} {sound:5.2f} {light:5.2f} "
                  f"{field.emitter_activation:5.2f}  {max_act:5.2f} "
                  f"{counts['active']:3d}/{counts['resting']:3d}/"
                  f"{counts['dormant']:3d}/{counts['deep_sleep']:3d} "
                  f"{state['metabolism']['energy_reserve']:6.2f} "
                  f"{state['metabolism']['memory_pressure']:4.2f} "
                  f"{live_links}/{total_links}")

    cells = field.cells
    print("\nAnchor cells:")
    for n in (SOUND_ANCHOR, EMITTER_ANCHOR, LIGHT_ANCHOR):
        c = cells[n]
        print(f"  C{n:<3} {c.type:<14} pos=({c.row},{c.col}) "
              f"act={c.activation:.3f} ripple={c.ripple:.3f} "
              f"energy={c.energy:.3f} fatigue={c.fatigue:.3f} "
              f"rel={c.relevance:.3f} state={c.state}")

    max_act = max(c.activation for c in cells.values())
    max_ripple = max(abs(c.ripple) for c in cells.values())
    print(f"\nMax activation: {max_act:.3f}")
    print(f"Max ripple: {max_ripple:.3f}")
    print(f"State counts: {field._state_counts()}")
    print(f"Scarred connections (at floor): "
          f"{sum(1 for w in field.weights.values() if w <= LIVE_LINK_THRESHOLD)}")
