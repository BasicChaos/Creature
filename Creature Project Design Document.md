Creature Project Design Document

Version: v05.4.1, June 13 2026
Status: current. This document matches the running code in Code/. It replaces the
earlier editions that described the 3-cell arousal/fatigue/tonic network and the
11-cell ring. Those were real earlier stages and are kept in git history and in
Obsidian/Creature Build Versions. The 111-cell field below is what runs today.

---

Runtime note: all Creature services (collector, field, dashboard) run on the
Raspberry Pi 3 B+. The Mac is only used for editing and Git. The ESP32 is the
body and runs no logic.

## Purpose

Creature is an experiment in building a small artificial organism, not an AI
assistant. The goal is a persistent entity that senses the world, builds internal
state, expresses itself through a physical output, and carries its history forward
as it grows. The bet underneath the project: persistent pressure on a connected
field of cells grows stable structure over time, and that structure is the memory.

Current work is about architecture, not intelligence. The organism is meant to
start simple and gain complexity without replacing its core.

## How it works right now

The Creature has a body and a mind.

The body is an ESP32-S3 with two senses and one output: a BH1750 light sensor, an
INMP441 microphone, and the onboard NeoPixel LED. It runs no logic. Ten times a
second it reads light and sound and streams one JSON line per sample to the Pi,
and it waits for `LED:<0-255>` commands telling the pixel how bright to be.

The mind is on the Pi. One program, the collector, runs this loop:

1. Read a sample line from the ESP (light_lux, sound_rms).
2. Normalize each sensor to a 0 to 1 value that adapts to the room.
3. Once per second, step the cell field on those two values.
4. Read the emitter cell's activation back out.
5. Send the matching LED brightness to the ESP.
6. Write a live snapshot for the dashboard and a history row to SQLite.

The field is the nervous system. It is 111 cells laid out as a flat sheet of
tissue, not a list of named feelings. Light comes in at one edge, sound at another,
and the LED is driven by a cell in the middle. Everything between them is free
tissue that pressure flows through, ripples cross, and learning slowly reshapes.

In one line: light and sound become pressure, pressure spreads through tissue,
the tissue reshapes itself with experience, and one cell in the middle drives the
light.

## Current hardware

Development machine: MacBook Air M1. Editing, Git, PlatformIO. Not in the runtime.

Runtime machine: Raspberry Pi 3 B+ (hostname creaturePi). Runs the collector, the
field, persistence, the SQLite history on the attached SSD, and the dashboard
server on port 8080.

Body node: ESP32-S3-DevKitC-1-N8R8.
- BH1750 ambient light sensor, I2C on pins 8 (SDA) and 9 (SCL).
- INMP441 MEMS microphone, I2S on pins 5 (SCK), 6 (WS), 7 (SD). The ESP computes
  a DC-removed RMS per sample.
- Onboard NeoPixel RGB LED on pin 38. The single emitter.
- Sampling at 10 Hz. One JSON line per sample over USB serial, and over a small
  WiFi TCP server on port 7777 with mDNS when WiFi is configured.

External: a Linux VPS (basicchaos.com) that hosts a static mirror of the
dashboard. It only receives an outbound copy from the Pi. It cannot reach back in.

## Architecture

```
BH1750 (light) + INMP441 (sound)
  -> ESP32-S3 body, 10 Hz JSON: {time_ms, light_lux, sound_rms}
  -> USB serial or WiFi TCP
  -> collector on the Pi
      -> two rolling normalizers (raw -> 0..1, adaptive to the room)
      -> field.step(sound, light) once per second
      -> emitter cell activation -> LED:<brightness> back to the ESP
  -> creature_state.json (live snapshot) + SQLite history
  -> dashboard server :8080  -> static export -> VPS mirror
```

The ESP is the body. The Pi is the mind. The VPS is an observation surface only.

## The cell field

File: Code/Python/mind/cell_field.py. The field steps once per second. Every
constant assumes that 1 Hz tick.

Layout. 111 cells in an organic 11-row shape inside a 13-column grid, widest in
the middle, a rough leaf or eye. Three cells are anchors with fixed roles:

- Sound anchor, cell 5, at the left edge. Receives the microphone.
- Light anchor, cell 33, at the right edge. Receives the BH1750.
- Emitter anchor, cell 29, in the centre. Drives the LED.

Every other cell is free tissue. Connections are local only: each cell links to
its immediate grid neighbours (radius 1), about 386 links in all, each starting at
weight 0.20. Distance is real. A signal has to travel cell to cell to cross the
sheet.

Each cell holds a few fast values and a few slow ones.

Fast state, recomputed every tick:
- pressure: incoming influence this tick, from the senses, from neighbours, and
  from the ripple wave.
- activation: the cell's current level. A leaky integrator. It decays toward zero
  and is driven up by pressure, scaled by the cell's homeostatic gain.
- ripple and ripple velocity: a wave layer kept separate from activation.
  Activation is local state; ripple is the passing wave that makes the sheet
  visibly respond. Ripple spreads as a weighted average across links, so it can
  cross even dead tissue, which is how dark regions get a chance to come back.

Slow state, persisted across restarts:
- energy and fatigue (metabolism, below).
- relevance: how connected and used a cell is, derived from its links.
- homeo_gain: the cell's homeostatic gain (below).
- the connection weights themselves, which are the long-term memory.

### Metabolism

Cells draw from a finite shared energy reserve, then spend their own local energy
to sense, process, and learn. Quiet cells refill slowly; active cells burn
through energy and build fatigue. When the field is mostly quiet, most cells sit
in low-energy states and the reserve recovers. When a lot is happening, energy
gets scarce and the field is pushed toward rest.

Four cell states follow from energy and recent activity: active, resting, dormant,
deep_sleep. Quieter states update less often and refill more slowly. This is a
scheduler, not a mood.

The shared reserve replenishes at a fixed rate per tick. See the v05.4.1 note
below for why that rate was raised.

### Learning and forgetting

Two changes accumulate in the connection weights.

Hebbian growth: when two linked cells are co-active and under real pressure at the
same time, their link strengthens (bounded at 2.0). Cells that fire together wire
together. Learning costs energy, so a starved field learns less.

Decay with a scar floor: every link loses a little weight each tick, fast for
fresh unused links and slow for well-used ones. A link never falls to zero. It
rests at a small scar floor (0.02). A scarred link carries almost no learned
drive, but ripples still pass through it, so a strong later event can drive both
ends again and bring it back. Pruning is demotion, not amputation.

Homeostatic gain is the stabilizer. Each cell tracks its own long-run activity and
adjusts its gain to hold that activity near a target (0.06). A busy region turns
its gain down; a quiet region turns it up. This is the one regulator kept against
runaway drive, on purpose. Earlier versions stacked several stabilizers that
cancelled out the effect of learning.

### Sleep and consolidation

The field sleeps when it is under-stimulated for a while, or when memory pressure
is high, or when the energy reserve runs low. During sleep it replays its most
significant recent events, re-touching the links those events ran through, and
demotes weak unused links toward the scar floor. Each replayed event is consumed
so one spike cannot dominate. Sleep is triggered consolidation, not a clock.

### Persistence

Code/Python/data/creature_field_state.json holds only the slow state: connection
weights, energy, fatigue, relevance, homeostatic gain, ages. It is written every
100 ticks, on clean shutdown, and via an atexit handler, and reloaded on boot. The
fast values (activation, ripple, fatigue spikes) are deliberately not saved, so
the creature wakes calm but keeps its slow self. A snapshot whose shape does not
match the current field (different cell count, newer format) is refused rather
than half-applied.

## Normalization and the noise gate

File: Code/Python/mind/normalize.py and the response curve in the collector.

Each sense is turned into a 0 to 1 value by a rolling normalizer: an EMA smooth
plus a rolling min and max over a recent window, with a minimum-range guard so a
flat signal reads near zero. This is how the creature habituates to its own room
instead of needing fixed calibration. Light uses a 120-second window; sound uses a
shorter 20-second window with heavier smoothing because it is spiky.

Sound then passes a response curve, "how strongly should the organism feel this",
with a floor, a gain, and an exponent. See the v05.4.1 note below for the floor
change.

Known limit: the rolling normalizer reports where the current value sits inside
its recent range, not how loud the room is in absolute terms. In a quiet room it
stretches small hum to fill the range. The response-curve floor gates that out for
the current room, but the durable fix is to log raw sound_rms (and light_lux) so
the normalizer can be calibrated against an absolute level. That logging is an
open item.

## v05.4.1 changes (June 13 2026)

Two fixes, both found by replaying the real 2026-06-12 overnight log through
tools/field_lab.py, both validated control versus variant before shipping.

The problem they fix. Overnight the field collapsed: live links fell from a full
386 to a 16-link skeleton, and the energy reserve sat pinned at zero for thirteen
hours. The cause was a loop, not a quiet room. The microphone read a silent dark
room as moderately loud (about 0.27 after the response curve), which kept cells
firing, which drained the shared reserve to zero, where it deadlocked because
income could not cover the draw of all those active cells. Chronic low-energy
sleep then applied its decay penalty and eroded the structure.

1. Energy deadlock. GLOBAL_REPLENISH_PER_TICK raised from 0.32 to 0.7 in
   cell_field.py. The reserve can now cover a realistic number of active cells and
   recover after a busy spell instead of locking at zero. In replay on the real
   overnight input the reserve held near 35 instead of 0, and low-energy sleeps
   dropped from 45 to 2.

2. Microphone noise gate. SOUND_RESPONSE_FLOOR raised from 0.03 to 0.20 in
   collector.py. The ambient median now gates to zero (about 89 percent of
   overnight ticks read silent in replay) while louder transients still pass
   (evenings still average about 0.06). Gain and exponent are unchanged so real
   events stay reactive.

Validation, all on the real overnight input, 12k ticks:

```
run                        energy reserve   live links   low-energy sleeps
control (old code)         0.0 deadlock     30           45
metabolism fix only        ~35 steady       76            2
mic gate only              recovers 0..24   ~100         15
both fixes                 ~35 steady       ~125          3
```

The residual link decline under that input is real starvation: one dead sense
(light at 0 all night) and one gated sense carry little for the field to hold.
That is the input problem v06 addresses, not a metabolism problem.

## The replay harness

File: Code/Python/tools/field_lab.py. Runs the field offline against synthetic
scenarios (day, bursts, quiet) or recorded sensor history from a database copy.
Reports live links, weight differentiation, railed homeostatic gains, sleep
counts, and energy. Overrides any field constant for one run, saves a result, and
diffs a variant against a saved control.

Rule: no tuning change ships without a control-versus-variant run. This is the
difference between "I think it is emerging" and "here is the control run." The
v05.4.1 fixes above were made this way.

## Dashboard

Served from the Pi on port 8080, mirrored to the VPS for remote viewing.

- server.py: standard-library HTTP server. Serves the page and JSON for the live
  field state and history.
- index.html: an SVG view of the 111-cell sheet, polling once a second. Cells show
  activation, state, and relevance; links show weight, with scarred links drawn as
  ghosts.
- export_static.py and sync_to_vps.sh: export the live data to static files and
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
      src/main.cpp                   BH1750 + INMP441 + NeoPixel, 10 Hz JSON
    Python/
      collector/collector.py         the runtime loop on the Pi
      mind/cell_field.py             the 111-cell field
      mind/normalize.py              rolling 0..1 normalization
      common/paths.py                shared file paths
      dashboard/                     server.py, index.html, static export, sync
      tools/field_lab.py             offline replay harness
      tools/visualize.py             field visualization
      data/                          SQLite history + field snapshot (gitignored)
  Hardware/                          KiCad schematics, wiring, photos
  Obsidian/                          notes, theory, per-version build docs
  Archive/                           earlier experiments and versions
```

## Design philosophy

The ESP stays simple and stable: read sensors, stream raw values, receive
commands, drive outputs. Nothing more. It does not store memory, make decisions,
or interpret meaning.

The Pi holds everything that changes: normalization, the field, learning,
expression, persistence. This lets the senses and the output stay stable while the
mind iterates in Python.

The body should stay dumb. The mind should stay changeable. That split is what
lets the Creature evolve without rewriting hardware.

## Known limitations

- Two scalar senses cap what the field can encode. With light and sound only, the
  achievable structure is mostly "distance from each anchor." Adding cells does not
  help; adding senses does.
- The two anchors sit far apart (sound at column 0, light at column 12) and sensor
  reach ends around four cells, so few or no cells receive both senses. Cross-modal
  structure, the most interesting thing the field could learn, has little path to
  form with the current layout.
- The normalizer discards absolute loudness (see Normalization). Log raw sensor
  values to enable proper calibration.
- The homeostatic gain rails high in a very quiet regime, because average activity
  sits below target when most cells sleep. Worth watching once raw logging lands.

## Roadmap

v06, more world, not more machinery. Grow the body, not the brain. The parts are
already owned (BME280, MPU-6050, HC-SR04, a MAX98357A amplifier with a speaker).

- A slow sense: BME280 temperature and pressure on the existing I2C bus. A real
  diurnal rhythm gives the field slow structure to hold overnight.
- An event sense that correlates with sound: motion. The first true cross-modal
  pattern the field can actually learn.
- A real emitter: the field is 2D but the output is one pixel. Use the speaker, or
  a short NeoPixel strip mapping field regions to pixels, so the creature's internal
  geography shows in the room.
- Placement matters: put new anchors so their reach overlaps existing ones, or the
  new senses just add more provinces that never talk to each other.

Keep the mind on the Pi. Moving the field to the ESP breaks the body/mind split,
kills iteration speed, and buys nothing; the field is a few percent of CPU at 1 Hz.

More ESPs as separate sense-bodies is the version of "more ESPs" worth doing, and
the firmware already supports it through the WiFi TCP bridge. Spatially separated
nodes (motion and mic by the door, light and temperature by the window) streaming
to one Pi mind is how spatial structure would form. That is v07, after one richer
single body shows cross-modal structure that survives a night and a replay test.

Do not add cells until weights demonstrably shape behavior and survive a night
with the current 111.
