Creature Project Design Document

Version: v06.6, June 28 2026
Status: current. This document matches the running code in Code/. It supersedes the
v05.4.1 edition, which described the 111-cell field, and the earlier editions that
described the 3-cell arousal/fatigue/tonic network and the 11-cell ring. Those were
real earlier stages. They are kept in git history and in the build-version notes.
The twelve-cell ring below is what runs today.

The longer theory notes, per-version build docs, and daily logs live in a local
Obsidian vault (`ObsidianCreature/`) that is not part of the public repository.
This document is the canonical, self-contained design.

---

Runtime note: all Creature services (collector, field, dashboard) run on the
Raspberry Pi 3 B+. The Mac is only used for editing and Git. The ESP32 is the body
and runs no logic.

## Purpose

Creature is an experiment in building a small artificial organism, not an AI
assistant. The goal is a persistent entity that senses the world, builds internal
state, expresses itself through a physical output, and carries its history forward
as it grows. The bet underneath the project: persistent pressure on a connected
field of cells grows stable structure over time, and that structure is the memory.

Current work is about architecture, not intelligence. The organism is meant to
start simple and gain complexity without replacing its core.

The Creature's one real enemy is entropy. Left alone in a steady room, a field of
simple cells decays toward a flat, even, quiet state. The earlier versions showed
it plainly: generic homogeneous connections, most of them lost overnight, arousal
surging on startup then settling toward zero. A bigger field did not fix it. Adding
cells only added more tissue to go uniform. v06 is built to pull the other way, and
everything in it serves that.

## How it works right now

The Creature has a body and a mind.

The body is an ESP32-S3 with four senses and two emitters. It runs no logic. About
ten times a second it reads its senses and streams one JSON line per sample to the
Pi, and it waits for commands telling its emitters what to do.

The mind is on the Pi. One program, the collector, runs this loop:

1. Read a sample line from the ESP (light, sound, motion, weather).
2. Normalize each sense to a 0 to 1 value that adapts to the room.
3. Once per second, step the cell field on those values.
4. Read the emitter cells' activation back out through the decoder.
5. Send the matching strip and voice frames to the ESP.
6. Record what the body expressed into the autobiography.
7. Write a live snapshot for the dashboard and a history row to SQLite.

The field is the nervous system. It is a ring of twelve outer cells with a six-cell
reservoir on the inside. Senses come in at their anchors, the two emitters are
driven by cells that read the reservoir, and learning slowly reshapes the parts that
are allowed to change.

In one line: senses become pressure, pressure spreads through the ring, an inner
reservoir holds the recent past, predictive cells pass on surprise, and two cells
in the ring drive the light and the voice.

## Current hardware

Development machine: MacBook Air M1. Editing, Git, PlatformIO. Not in the runtime.

Runtime machine: Raspberry Pi 3 B+ (hostname creaturePi). Runs the collector, the
field, persistence, the SQLite history on the attached SSD, and the dashboard
server on port 8080.

Body node: ESP32-S3-DevKitC-1 (N8R8).

Senses:

- BH1750 ambient light, I2C, address 0x23.
- INMP441 MEMS microphone, I2S0. The ESP computes a DC-removed RMS per sample.
- MPU-6050 / ICM-20689 motion, I2C, address 0x68, reduced to one motion scalar.
- BME280 temperature and pressure, I2C, address 0x76. A slow sense with a real
  day-night rhythm that stays nonzero at night, so the field has something to hold
  when light reads zero in the dark.

Emitters:

- SK6812 RGBW strip, about 16 pixels, on GPIO 4 through a 470 ohm resistor. The
  field's skin and face. Driven by `PIX:` frames.
- MAX98357A amplifier and a small speaker, I2S1. The field's voice. Driven by
  optional `VOX:` tones.
- Onboard NeoPixel on GPIO 38. A status pixel, driven by the legacy `LED:` command.

Sampling at about 10 Hz. One JSON line per sample over USB serial, and over a small
WiFi TCP server on port 7777 with mDNS when WiFi is configured. WiFi credentials are
kept out of git in an untracked `creature_wifi_secrets.h`. Full pin assignments,
power, and gotchas are in `Hardware/Creature v06/WIRING v06.md`.

External: a Linux VPS (basicchaos.com) that hosts a static mirror of the dashboard.
It only receives an outbound copy from the Pi. It cannot reach back in. The
deployment target is read from an untracked config file.

## Architecture

```
BH1750 (light) + INMP441 (sound) + IMU (motion) + BME280 (weather)
  -> ESP32-S3 body, ~10 Hz JSON
  -> USB serial or WiFi TCP
  -> collector on the Pi
      -> rolling normalizers (raw -> 0..1, adaptive to the room)
      -> field.step(...) once per second
          -> twelve-cell ring (predictive cells)
          -> six-cell fixed reservoir
          -> trained readout -> two emitter cells
      -> expression decoder -> PIX: strip frame + VOX: tone
      -> autobiography record
  -> creature_state.json (live snapshot) + SQLite history
  -> dashboard server :8080  -> static export -> VPS mirror
```

The ESP is the body. The Pi is the mind. The VPS is an observation surface only.

## The cell field: a ring of twelve

File: `Code/Python/mind/cell_field_v06.py`. Version string `v06.6-predictive`. The
field steps once per second. Every constant assumes that 1 Hz tick.

The outer ring is twelve cells: six sense and emitter anchors alternating with six
in-between cells. The ring order is the design, because adjacency is what mixes. Two
rules set it: put correlated senses next to each other, and put each emitter next to
its own sense so the loop is built into the anatomy.

| Position | Anchor          | In-between to next | What that cell is           |
|----------|-----------------|--------------------|-----------------------------|
| 1        | Speaker (out)   | Speaker x Sound    | hears its own voice (loop)  |
| 2        | Sound (in)      | Sound x Motion     | sound and motion (paired)   |
| 3        | Motion (in)     | Motion x LED       | movement drives light       |
| 4        | LED strip (out) | LED x Light        | sees its own light (loop)   |
| 5        | Light (in)      | Light x Weather    | light and weather (paired)  |
| 6        | Weather (in)    | Weather x Speaker  | the one weak gap            |

Two of the six in-between cells are the loop made internal: the cell between the
speaker and the mic hears the Creature's own voice, the cell between the strip and
the light sensor sees its own light. Both correlated sense-pairs get a dedicated
cell. One gap, weather to speaker, is weak, the honest cost of closing a ring. The
two emitter anchors are the expression cells.

Why a ring and not a full mesh. A mesh connects every pair through its own cell, so
every in-between cell touches hardware on both ends. It is wide and shallow, a
switchboard with no interior, and depth has nowhere to live in a switchboard. The
ring keeps an interior, the reservoir, which is where signal history accumulates
without being tied to a single sensor.

Hold the field at twelve outer cells. Do not grow until the weights demonstrably
shape behavior and survive a night.

## The predictive cell

The outer ring cells are predictive cells, not leaky integrators. Each cell holds a
running prediction of its own drive and reports the error, the surprise, instead of
the raw input. A cell that predicts well goes quiet. A surprised cell speaks. This
is the negentropy engine in miniature: the cell builds a model and feeds on
surprise. The unit is prediction error, not a firing rate.

This is what fights entropy at the cell level. Under steady input the old leaky
field flattens: every cell's average activity collapses to one shared value, a
uniform wash. The predictive field holds many times that spread. On a realistic day
run its overall differentiation is about 0.44 against the leaky field's 0.15.

The homeostatic gain that the 111-cell field used was removed here, because that was
the part actively pulling every cell to one shared level. The predictive cell does
not need it.

One honest consequence. The predictive cell does not rescue the light-and-weather
side, and it should not. Slow steady signals are easy to predict, so a surprise cell
correctly goes quiet on them. Weather is meant to be a nonzero floor at night, not a
structure builder. Keeping that side alive is the closed loop's job, not the cell's.

## The reservoir

The six inner cells are a reservoir: a fixed recurrent substrate whose weights are
set once at init and never updated by learning. The outer in-between cells drive it.
It holds a time-delayed, high-dimensional echo of recent sensor history.

Why fixed weights. A learned reservoir homogenizes for the same reason the outer
field did: co-activation flattens everything toward the mean. Fixing the weights
removes that pressure from the interior. The richness comes from random sparse
connectivity, not from learning. The echo state property means any sufficiently
varied input history produces a distinguishable reservoir state.

Spectral radius. The reservoir's weight matrix must have a spectral radius below 1.0
for the echo state property to hold, so activity echoes without blowing up. A value
around 0.9 is a reasonable start. In the sweep the reservoir is healthy from 0.1 to
1.1 and breaks at 1.5. The spectral radius is the lever that matters, not the input
scale.

What it gives the Creature: temporal memory without weight explosion, cross-modal
mixing without proximity constraints, and a clean separation between the processing
layer (reservoir, fixed) and the learning layer (readout, adaptive). What it does
not give: it does not replace the loop. The loop is still the primary entropy
fighter. The reservoir just gives the emitters richer material to read.

## The emitter readout

The two emitter anchors (speaker, strip) are the only nodes whose outgoing weights
are trained. Each reads a learned linear combination of the reservoir state and
learns, by a running delta rule, which combination to express.

The reservoir read beats a direct connection to the emitter's ring neighbours. A
direct neighbour tap carries almost no information about either sense. The reservoir
gives the emitter a far richer drive.

The training target is the current sense the emitter should express, not the next
raw sense, because sound is random bursts and cannot be predicted. When the loop
closes, the same rule becomes real prediction, because the sense it confirms will be
the emitter's own returning output. The reservoir does not change. Only the readout
does.

## Metabolism

Cells draw from a finite shared energy reserve, then spend their own local energy to
sense, process, and learn. Quiet cells refill slowly. Active cells burn through
energy and build fatigue. When the field is mostly quiet, the reserve recovers. When
a lot is happening, energy gets scarce and the field is pushed toward rest.

Four cell states follow from energy and recent activity: active, resting, dormant,
deep_sleep. Quieter states update less often and refill more slowly. This is a
scheduler, not a mood.

The shared reserve was resized for a twelve-cell body, where most cells stay active,
unlike the mostly dormant 111-cell field. At the old sizing the reserve deadlocked
at zero and learning starved. The twelve-cell values are start 4.0, max 6.0, refill
0.6 per tick.

## Learning and forgetting

Two changes accumulate in the trainable connection weights.

Hebbian growth: when two linked cells are co-active and under real pressure at the
same time, their link strengthens. Cells that fire together wire together. Learning
costs energy, so a starved field learns less.

Decay with a scar floor: every trainable link loses a little weight each tick, fast
for fresh unused links and slow for well-used ones. A link never falls to zero. It
rests at a small scar floor. A scarred link carries almost no learned drive, but
ripples still pass through it, so a strong later event can bring it back. Pruning is
demotion, not amputation.

The reservoir is exempt from all of this. Its weights are fixed by design.

## Sleep and consolidation

The field sleeps when it is under-stimulated for a while, or when memory pressure is
high, or when the energy reserve runs low. During sleep it replays its most
significant recent events, re-touching the links those events ran through, and
demotes weak unused links toward the scar floor. Each replayed event is consumed so
one spike cannot dominate. Sleep is triggered consolidation, not a clock.

v06.6 fixed a real bug here: sleep was never triggering because the quiet-pressure
threshold that armed it was unreachable in the twelve-cell body. The threshold was
corrected so consolidation can fire as designed.

## Expression

File: `Code/Python/mind/expression_v06.py`. The field is read once per tick,
read-only, and four signals come out:

- Arousal: overall activation gated by energy. Drives a white glow and the voice
  volume.
- Balance: warm senses against cool senses. Drives strip color and voice pitch.
- Tempo: from ripple. Drives shimmer, a travelling pulse, and voice roughness.
- Spatial profile: along the sound-to-light axis. Drives per-pixel color, so the
  strip shows the field's own geography.

The body renders `PIX:` and `VOX:` lines and decides nothing. The preview tool,
`Code/Python/tools/expression_preview.py`, runs this map offline, renders the strip,
and synthesizes the voice. It proved the map reads the field correctly. It also
showed the field going quiet, which is the problem the loop and the reservoir exist
to answer.

## Expression as memory

File: `Code/Python/mind/expression_memory_v06.py`. This is the v06.5/v06.6 layer.

Each tick the Creature's own expression (arousal, balance, tempo, whether it voices)
becomes a point in a graph. Transitions between points are weighted, unused paths
decay. The graph is an autobiography. Two creatures that lived different days end up
with clearly different graphs, far enough apart that the graph is a usable identity,
while two days with different random seeds stay close. The graph encodes the life,
not the noise.

Three findings shaped the layer. On its own, letting the graph steer the next
expression turns it into memory but has no safe middle: a little habit sharpens
motifs, more habit collapses the Creature into one groove. A novelty drive fixes
that, but only if it is adaptive, firing where the Creature is worn in. Then a band
opens at moderate habit where motifs survive without collapse. That band is
temperament.

In the runtime today, only the record layer is live, behind `CREATURE_EXPR_MEMORY`
(default on). It is passive: it changes nothing about what the body does. It records
each tick, saves the autobiography alongside the field slow-state, reloads it on
start, and summarizes it in the dashboard snapshot under `expression_memory`. Bias
and novelty steering are deliberately not wired to the body yet. Steering needs a
decoder that renders from a steered signal, and that is a later increment.

## The loop

Place one light sensor where it can see the strip. Place the mic where it can hear
the speaker. Now the Creature's own output returns as input. When it lights up, it
sees its own light. When it sounds, it hears itself. Action causes sensing. This is
the move that lets the Creature generate its own activity instead of waiting for the
room to hand it some.

The ring already prepares this. The strip sits next to the light sensor, the speaker
next to the mic, so the loop exists in the body plan before it exists in the room.
The physical placement completes it.

Start the coupling loose. A tight loop runs away, the light driving the sensor
driving the light. Begin with the perceiving sensor weak or slightly off the
emitter's direct line, and let the coupling grow. The aim is a Creature that can
sustain itself, not one that screams into its own eye.

In simulation the loop works. With a curiosity drive that pokes when the Creature is
flat and eases off when it is active, a loose loop gain makes a relaxation
oscillator, bounded by construction yet never fully still. In a dark, silent room
the open-loop control goes flat (mean arousal about 0.008) while the looped, curious
Creature stays alive (about 0.20), restless (tail std about 0.06), and bounded
(steady-state max under 0.85), and its forward-model error falls to zero. Nothing in
the room caused any of it. The activity is self-generated and learned. One caution
from the same runs: a steady self-loop is as predictable as a steady room, so the
predictive field habituates to it and goes quiet unless the probe stays
unpredictable. Random bursts keep surprise alive.

## The stance on going quiet

The field is built to stop reacting once nothing changes. That is correct for a
filter and wrong for a creature meant to stay alive. The project takes a position:
do not tune the Creature to silence. Let arousal persist. Let it stay restless, let
it stay bored in the way that drives it to act rather than the way that drops it to
zero. The loop gives it the means, the reservoir gives it history to act on, this
stance gives it the permission. In practice the energy gate must not be the dominant
voice. It can dim a truly drained Creature, but a healthy one in a dull room should
still hum, fidget, and probe through the loop.

## Persistence

`Code/Python/data/creature_field_state.json` holds only the slow state: trainable
connection weights, reservoir weights, energy, fatigue, relevance, ages, and the
predictive cells' learned predictions. It is written periodically, on clean
shutdown, and via an atexit handler, and reloaded on boot. Fast values (activation,
ripple) are deliberately not saved, so the Creature wakes calm but keeps its slow
self. A snapshot whose shape does not match the current field is refused rather than
half-applied. The autobiography is persisted alongside it and reloaded the same way.

## Normalization and the noise gate

File: `Code/Python/mind/normalize.py` and the response curve in the collector.

Each sense is turned into a 0 to 1 value by a rolling normalizer: an EMA smooth plus
a rolling min and max over a recent window, with a minimum-range guard so a flat
signal reads near zero. This is how the Creature habituates to its own room instead
of needing fixed calibration. Sound then passes a response curve with a floor, a
gain, and an exponent, so ambient hum gates out while real transients pass.

Known limit: the rolling normalizer reports where the current value sits inside its
recent range, not how loud the room is in absolute terms. The durable fix is to log
raw values so the normalizer can be calibrated against an absolute level.

## The replay harness

File: `Code/Python/tools/field_lab_v06.py`. Runs the field and the expression layer
offline against synthetic scenarios or recorded history, one gate per build step.
The matrix maths is plain Python, no numpy, so the numbers are identical on any
machine and the runs reproduce exactly. Every gate uses a fixed seed.

| Gate | What it proves | Command | Result |
|------|----------------|---------|--------|
| ring | ring shapes structure, survives a night | `--gate --set CELL_MODEL=leaky` | 6/7 |
| reservoir | distinguishes histories, echo state holds | `--reservoir` | 4/4 |
| readout | richer than a direct ring tap | `--readout` | 2/2 |
| predictive | no flattening under steady input | `--predictive` | 2/2 |
| record | an autobiography forms and tells two lives apart | `--exprmem` | 3/3 |
| bias | habit becomes memory, over-bias collapses | `--exprbias` | 2/2 |
| novelty | adaptive novelty opens a temperament band | `--exprnov` | 2/2 |
| dark-room | self-generated, bounded, learned activity | `--darkroom` | 5/5 |

Rule: no tuning change ships without a control-versus-variant run. This is the
difference between "I think it is emerging" and "here is the control run."

## Dashboard

Served from the Pi on port 8080, mirrored to the VPS for remote viewing.

- `server.py`: standard-library HTTP server. Serves the page and JSON for the live
  field state and history.
- `index.html`: an SVG view of the ring and reservoir, polling once a second. Cells
  show activation, state, and relevance; links show weight, with scarred links drawn
  as ghosts.
- `export_static.py` and `sync_to_vps.sh`: export the live data to static files and
  rsync them to the VPS over SSH as a restricted user. The Pi is never exposed to
  the internet; it only makes outbound connections.

The dashboard is an output channel and a microscope. It is not part of sensing,
memory, or decision-making, and nothing that exists only for the dashboard should
shape field design.

## Repository structure

```
Creature/
  Code/
    Firmware/esp-creature-core/      ESP32-S3 body (PlatformIO)
      src/main.cpp                   four senses + two emitters, ~10 Hz JSON
    Firmware/bench/                  per-sensor bring-up sketches
    Python/
      collector/collector.py         the runtime loop on the Pi
      mind/cell_field_v06.py          the ring, reservoir, predictive cell, readout
      mind/expression_v06.py          the decoder (field -> PIX/VOX)
      mind/expression_memory_v06.py   the autobiography layer
      mind/normalize.py               rolling 0..1 normalization
      mind/cell_field.py              the retired 111-cell field, kept for reference
      dashboard/                      server.py, index.html, static export, sync
      tools/field_lab_v06.py          offline replay and gate harness
      tools/expression_preview.py     renders the decoder offline
      data/                           SQLite history + field snapshot (gitignored)
  Hardware/                          KiCad schematics, wiring, bench notes, photos
  Archive/                           earlier experiments and versions (gitignored)
```

## Design philosophy

The ESP stays simple and stable: read sensors, stream raw values, receive commands,
drive outputs. Nothing more. It does not store memory, make decisions, or interpret
meaning. It may have body-level safety reflexes later, such as capping brightness on
low battery, because those protect hardware.

The Pi holds everything that changes: normalization, the field, learning,
expression, persistence. This lets the senses and the output stay stable while the
mind iterates in Python.

The body should stay dumb. The mind should stay changeable. That split is what lets
the Creature evolve without rewriting hardware.

## Known limitations

- The loop is validated in simulation, not yet on hardware. The physical light and
  sound loops are the next real test, and the result the project most needs.
- The light-and-weather side stays weak by design, because slow steady signals
  produce no surprise. Only the loop keeps that side alive.
- Bias and novelty steering are not wired to the body, so the live Creature records
  its autobiography but is not yet steered by it.
- Metabolism is simulated while the body runs on USB power. Scarcity becomes real
  only when the battery and fuel gauge land.
- A steady self-loop is as predictable as a steady room, so the predictive field
  habituates to it unless the probe stays unpredictable.

## Roadmap

The lesson of v06 holds: the organism becomes more alive when action returns through
the world, not when the model grows more complicated. The hardware roadmap follows
from that. Detail is in `Creature v07 Hardware Potential.md`.

- Give v06 a body. Build a rigid carrier that fixes sensor and emitter placement, so
  the loop can be tuned from weak to strong physically instead of by luck.
- Characterize the existing loops. Measure that a known strip frame moves the light
  reading and a known tone moves the mic or the IMU.
- Battery as real metabolism. A LiPo, a load-sharing charger, and a MAX17048 fuel
  gauge on the existing I2C bus, so expression costs energy and feeding the Creature
  becomes a real event.
- Haptics before locomotion. A small vibration actuator the IMU can feel, a safe
  action-sense loop without wheels.
- Touch and skin. Capacitive pads as an event channel, so human contact becomes part
  of the body loop.

Keep the mind on the Pi. Moving the field to the ESP breaks the body/mind split,
kills iteration speed, and buys nothing. The field is a few percent of CPU at 1 Hz.

More ESPs as separate sense-bodies is the version of "more ESPs" worth doing, and
the firmware already supports it through the WiFi TCP bridge. Spatially separated
nodes streaming to one Pi mind is how spatial structure would form. That comes after
one richer single body shows structure that survives a night and a replay test.

Do not add cells, senses, or motors casually. Prove one part on the bench, prefer
closed loops over passive inputs, and prefer physical consequence over a richer
dashboard.
