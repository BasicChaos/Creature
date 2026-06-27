# Creature v06.5 expression-memory and loop results

Version: v06.5 sim phase, June 27 2026. Status: results log.

Records the build and test of the expression-memory layer (record, bias, novelty)
and the closed-loop dark-room probe, all in simulation in `field_lab_v06`. Pairs
with the design `Creature Expression as Memory.md` and the direction
`Creature Three Evolutions Out.md`. No hardware moved.

## The short version

Four offline probes were built and each passed its own gate, with a fixed seed so
the runs repeat.

1. The creature can keep an autobiography. Each tick its own expression (arousal,
   balance, tempo, whether it voices) becomes a point in a graph. Two creatures
   that lived different days end up with clearly different graphs, so the graph is
   a usable identity.
2. Letting the graph steer the next expression turns it into memory, but on its
   own memory has no safe middle. A little habit sharpens motifs; more habit
   collapses the creature into one groove.
3. A novelty drive fixes that, but only if it is adaptive, firing where the
   creature is worn in. Then a band opens at moderate habit where motifs survive
   without collapse. That band is temperament.
4. With the loop closed in sim and a curiosity drive, the creature stays active in
   a dark, silent room where the same creature without the loop goes flat. The
   activity is bounded, restless, and it learns its own echo.

Everything below runs from `Code/Python`. All probes use `--seed`.

## What changed in the code

One file: `Code/Python/tools/field_lab_v06.py`. Four probes and their helpers were
added. The brain (`mind/cell_field_v06.py`) and the decoder
(`mind/expression_v06.py`) were not touched; the probes read them. Plain Python,
no numpy, so the runs reproduce.

## Step 1: record (the autobiography)

    python3 tools/field_lab_v06.py --exprmem --seed 1

Result: 3 of 3 checks pass.

The expression vector is what the body emits, taken straight from the decoder:
arousal, balance, tempo, and whether it would send a voice tone. Each is
grid-quantized into a node, transitions between nodes are weighted, unused paths
decay. Identity is the total-variation distance between two graphs. A day life and
a bursts life sit 0.66 apart. Two day lives with different seeds sit 0.06 apart,
about an elevenfold gap. The graph encodes the life, not the noise.

## Step 2: bias (memory that steers)

    python3 tools/field_lab_v06.py --exprbias --seed 1

Result: 2 of 2 checks pass.

The graph predicts the usual next state, and that prediction is blended into the
expression by one mixing weight, field against habit. Sweeping the weight: nodes
fall from 49 to 4, dwell rises from 0.85 to 1.0, visit-entropy collapses from 0.73
to 0.001. There is no stable middle. Even mild bias trends toward the groove.
Memory on its own eats the creature, which is why novelty has to be a force, not a
readout.

## Step 3: novelty (the counter-force)

    python3 tools/field_lab_v06.py --exprnov --seed 1

Result: 2 of 2 checks pass.

Constant novelty was bistable, either too weak to leave the groove or strong enough
to raster the whole space into pure wandering. Adaptive novelty, gated by a dwell
counter so it fires only where the creature is worn in, opens a middle. Mapping
habit against novelty, a temperament band sits at moderate habit (around w 0.6, n
0.2 to 0.4): motifs about 1.7 times sharper than pure field while the spread stays
healthy. Nothing rescues extreme habit. Once fully in the groove, the dominant
habit recaptures every escape.

## The loop and the dark-room probe

    python3 tools/field_lab_v06.py --darkroom --seed 1

Result: 5 of 5 checks pass, stable across seeds 1, 2, 3, 7.

This probe feeds the body's output back into the field, which the memory steps
never did. The creature's own light and voice return as light and sound input. A
curiosity drive gates on current arousal, poking when the creature is flat and
easing off when it is active. With a loose loop gain that makes a relaxation
oscillator, bounded by construction yet never fully still. A small forward model
learns the loop gain.

In a dark, silent room the open-loop control goes flat (mean arousal 0.008). The
looped, curious creature stays alive (about 0.20), restless (tail std about 0.06),
bounded (steady-state max under 0.85), and its forward-model error falls to zero.
Nothing in the room caused any of it. The activity is self-generated and learned,
not random.

Two findings worth keeping. The loop runs away if coupled tightly, so keep it
loose, as the design said. And a steady self-loop is as predictable as a steady
room, so the predictive field habituates to it and goes quiet unless the probe
stays unpredictable. Random bursts keep surprise alive.

## Gate summary

| Probe | What it proves | Command | Result |
|-------|----------------|---------|--------|
| record | an autobiography forms and tells two lives apart | `--exprmem` | 3/3 |
| bias | habit becomes memory, and over-bias collapses | `--exprbias` | 2/2 |
| novelty | adaptive novelty opens a temperament band | `--exprnov` | 2/2 |
| dark-room | self-generated, bounded, learned activity in an empty room | `--darkroom` | 5/5 |

## Into the runtime (v06.5 software)

The record layer is now in the live creature, not just the gates. The validated
logic moved into `Code/Python/mind/expression_memory_v06.py`, the single source of
truth; `field_lab_v06` imports its primitives (all four gates still pass) and the
collector imports the same module. Each tick the collector records what the body
expressed into a lasting autobiography, saved alongside the field slow-state and
reloaded on start, with a summary in the dashboard snapshot under
`expression_memory`. It is behind `CREATURE_EXPR_MEMORY` (default on) and is
passive: it changes nothing about what the body does.

Bias and novelty steering, and the loop, are deliberately not wired to the body
yet. Steering needs a decoder that renders from a steered signal, and the loop
needs the hardware sensors placed and stable power. Those are the next increments.

## Where this leaves the project

The whole sim arc stands. Record, bias, novelty, and the closed loop with its
dark-room proof all run in `field_lab` and gate green. This is the rehearsal.

The real build for Evolution 1 is to place the two loop sensors physically (a light
sensor that sees the strip, the mic positioned to hear the speaker), wire the
curiosity drive and forward model into the firmware and collector, and run the
dark-room test on the actual creature. That hardware run is the result the Risks
doc has been asking for. The one blocker is power: the loop work waits on the new
PowerBoost going in. Right now the 5V rail is fed from a second ESP.

Two threads stay in sim behind the hardware loop. Expression-memory step 4 (couple
the graph's prediction error back into the field) waits by design until the loop is
proven. And coupling the step-3 novelty force into the loop is what would turn the
dark-room hum into genuine restlessness.
