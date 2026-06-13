# Creature v05.4

## Overview

v05.4 keeps the 111-cell body and v05.3's scarcity, and makes structure matter.

v05.3 introduced metabolism, sleep, and structural memory. Simulation analysis of long runs revealed three problems:

```text
Decay beat learning: 70-90% of links pruned within hours regardless of input.
Weight changes barely affected behavior: drive was averaged, budgeted, and
  homeostatically cancelled.
Pruned-to-zero regions could never regrow: no signal could reach them.
```

The field was built to grow structure and simultaneously built to neutralize it.

v05.4 removes the contradictions.

## Core Principle

A pathway that strengthened must carry more signal.

A memory that consolidated must survive silence.

A region that died must remain reachable.

A cell that runs out of energy must fall back to repair, not exile.

## Changes

### Drive is a sum, not an average

```text
v05.3: neighbor drive = weighted average of neighbor activations
v05.4: neighbor drive = clamped weighted sum
```

With an average, uniform weight growth changed nothing; learning could only
redistribute signal, never increase it. With a sum, a strengthened pathway
delivers more drive. Hebbian growth now changes what the field does, not just
how it routes.

The per-cell connection budget is removed. Homeostatic gain is the one
remaining stabilizer against runaway drive, and its target is now reachable
(0.06 instead of an unreachable 0.12 that left gains railed at maximum).
Railed gain count is reported in metabolism stats and on the dashboard.

### Pruning is demotion, not amputation

```text
v05.3: pruned links cut to weight 0.0 - permanent disconnection
v05.4: decayed links rest at a scar floor (0.02)
```

Scarred links carry almost no learned drive (live pathways reach 2.0, a 100x
range), but ripples still pass through scarred tissue because ripple
propagation is a weighted average, where a uniform floor cancels out. A strong
real-world event can therefore reach dark regions and recolonize them.
Reconnection happens when something significant happens - not spontaneously,
and not never.

Links above 0.05 count as live; at or near the floor they count as scarred
(the dashboard draws them as ghosts).

### Deep sleep is repair, not exile

Hardware anchors are no longer exempt from the energy rule. The sensor stream
is still read every tick, and pressure/ripple still move through the field, but
active cell work waits until the local cell has rebuilt enough charge. Refill
priority now favors depleted cells before already-running cells, so deep sleep
is net-positive recovery instead of a permanent lockout.

This mirrors the scar-floor rule for links: low-energy tissue is demoted to a
quiet, conductive, repairing state; it is not forced awake, and it is not cut
off forever.

### Sensor gain lives at the boundary

The field should not be retuned around a weak microphone. The collector keeps
the rolling normalized mic value as `sound_linear`, then applies a small
response curve before writing `sound_norm` into the organism. Quiet values stay
quiet; direct/loud events saturate nearer the top of the 0-1 range.

### Memory survives quiet

Three timescales now separate cleanly:

```text
Fresh unused links     sink to the scar floor within hours.
Occasionally used      hold while rehearsed, fade over ~a day of quiet.
Well-consolidated      survive a day of total silence at most of their weight.
```

Mechanisms: stability follows a quadratic law dominated by usage (which never
expires), pressure association now fades over ~10 hours instead of ~15
minutes, and full stability cancels the entire unused-decay penalty
(v05.3 capped protection at 0.85, so even the best pathway lost ~3% per quiet
hour). Sleep replay re-touches whatever stays significant, and each replayed
event is consumed so one spike cannot dominate consolidation.

## Measured results (tools/field_lab.py)

Synthetic day scenario, 12k ticks, against the v05.3 control:

```text
Live links:        stabilize near 140/386 (v05.3: thrashing collapse to ~10)
Differentiation:   near-anchor weights 3.4x far weights
Input-dependence:  sound-heavy vs light-heavy input produces measurably
                   different weight maps (not just the layout baseline)
Persistence:       all strong pathways survive 2.5h of pure silence at ~80%
Homeostat:         railed gains 17 -> 0-1
Reproducibility:   same seed + same input = identical field
```

## The replay harness

```text
Code/Python/tools/field_lab.py
```

Runs the field offline against synthetic scenarios (day / bursts / quiet) or
recorded sensor history from a database copy. Reports live links, weight
differentiation, railed gains, sleep counts; saves full results; diffs a
variant against a saved control.

No tuning change ships without a control-vs-variant run. This is the
difference between "I think it is emerging" and "here is the control run."

## Compatibility

```text
Snapshot format unchanged (version 5). v05.3 snapshots load; hard-zero
pruned links are migrated up to the scar floor on restore.
Database schema unchanged. The metabolism summary gains homeo_gain_railed.
Collector, server, ESP firmware: unchanged.
```

## Success Criteria

```text
Live link count levels off instead of trending to zero.
Weight map reflects input statistics, verified by control runs.
Strong pathways survive a quiet day.
Railed homeostatic gains stay near zero.
Scarred regions regrow after strong events.
```

## What v05.4 does not do

No new senses, no new cell behaviors, no growth/mitosis/death. With two
scalar inputs, the achievable structure is limited by the world, not by the
field. That is the v06 problem: more world, not more machinery.
