Creature Project Design Document

Version: v07.0, October 4 2026
Status: current. This document matches the running code in Code/. It supersedes the
v05.4.1 edition, which described the 111-cell field, and the earlier editions that
described the 3-cell arousal/fatigue/tonic network and the 11-cell ring. Those were
real earlier stages. They are kept in git history and in the build-version notes.
The twelve-cell ring below is what runs today.

v07.0 is the v06.9 mind on a body with more on it: two air sensors and an e-paper
readout, added on 4 October 2026. Neither is part of the Creature's flow yet. The
field is unchanged, and the code files keep their `v06` names.

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

The body is an ESP32-S3 with four senses and two emitters, plus two air sensors and
an e-paper readout that the field does not use. It runs no logic. About
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

Beside that loop, and apart from the field, the collector passes the air readings
to the dashboard, works out a gas index from them, and every three minutes sends
the body ten short lines of text for its e-paper.

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

Body node: ESP32-S3-DevKitC-1 style board. The module is marked N16R8.

Senses:

- BH1750 ambient light, I2C, address 0x23.
- INMP441 MEMS microphone, I2S0. The ESP computes a DC-removed RMS per sample.
- MPU-6050 / ICM-20689 motion, I2C, address 0x68, reduced to one motion scalar.
- BME280 temperature and pressure, I2C, address 0x76. A slow sense with a real
  day-night rhythm that stays nonzero at night, so the field has something to hold
  when light reads zero in the dark.
- SCD4x CO2, temperature and humidity (I2C, address 0x62) and SGP41 VOC and NOx
  (I2C, address 0x59), wired on 4 October 2026 on the same bus. The body streams
  them raw (`co2_ppm`, `air_temp_c`, `humidity_pct`, `voc_raw`, `nox_raw`) and the
  collector passes them to the dashboard's sensor panel and `/api/sensors`. The
  field does not read them. The CO2 sensor is an SCD41.
  The SGP41's counts are resistances, not concentrations (the VOC count falls
  with more gas, the NOx count rises), so the collector also works out
  Sensirion's gas index from them once a second: VOC 100 is this room's normal,
  NOx 1 is normal. The maths is `mind/gas_index.py`, a plain-Python port of
  Sensirion's C, checked against it by `tools/gas_index_check.py`. Its learned
  baseline is saved beside the field state (`..._air_v06.json`) and taken back
  at start if it is under 12 hours old. A row of air readings goes into the
  `air_log` table every 20 ticks, which feeds the last-hour lines on the sensor
  panel through `/api/air_history`.
- MAX17048 fuel gauge (I2C, address 0x36), wired on 6 October 2026 in line
  between the LiPo and the PowerBoost. The body reads it once a second and
  streams `battery_v`, `battery_pct` and `battery_rate` (percent per hour,
  negative while discharging). The gauge is powered by the cell, so the three
  keys drop out of the sample lines when the cell is out. The collector passes
  them to the sensor panel, `/api/sensors` and the e-paper, and writes a row to
  the `power_log` table every 20 ticks. The field does not read them yet: this
  is the first of three steps toward the battery setting the field's energy
  income.

Emitters:

- SK6812 RGBW strip, about 16 pixels, on GPIO 4 through a 470 ohm resistor. The
  field's skin and face. Driven by `PIX:` frames.
- MAX98357A amplifier and a small speaker, I2S1. The field's voice. Driven by
  optional `VOX:` tones: `VOX:freq,ms,vol`, and from the firmware of 7 October
  2026 four more numbers that may be left off, `attack_ms,release_ms,h2,h3`: how
  long the tone takes to rise and to die away, and how much of the second and
  third overtone is mixed in. Left off, it is the plain beep as before. The
  mind does not send them yet; `tools/voice_audition.py` does, to choose sounds
  by ear.
- Onboard NeoPixel on GPIO 38. A status pixel, driven by the legacy `LED:` command.
- Waveshare 2.13inch e-Paper HAT V4, SPI, on the ESP's free edge: DIN 1, CLK 2,
  CS 42, DC 41, RST 40, BUSY 39. A readout, not part of the field's expression.
  Driven by `EPD:` text: two columns of five short lines, which the collector
  composes every 180 ticks (air readings on the left, the Creature's energy,
  memory pressure, emitter, cell states and live links on the right; the battery
  takes the emitter's line when the body has a fuel gauge). The body
  only draws the text, in a task of its own so the sample loop never waits for a
  refresh, and answers with a `paper` line giving the refresh time.
  `CREATURE_PAPER=0` at the collector turns it off.

Sampling at about 10 Hz. One JSON line per sample over USB serial, and over a small
WiFi TCP server on port 7777 with mDNS when WiFi is configured. After each `VOX:`
tone the body also sends one `vox` line: what the mic heard while the tone played,
measured at the tone's own pitch, next to the same measure of the room just before. WiFi credentials are
kept out of git in an untracked `creature_wifi_secrets.h`. Full pin assignments,
power, and gotchas are in `Hardware/Creature v06/WIRING v06.md`.

External: a Linux VPS (basicchaos.com) that hosts a static mirror of the dashboard.
It only receives an outbound copy from the Pi. It cannot reach back in. The
deployment target is read from an untracked config file.

## Architecture

```
BH1750 (light) + INMP441 (sound) + IMU (motion) + BME280 (weather)
  [+ SCD41 and SGP41 (air), MAX17048 (battery): carried along, not read by the field]
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
      [-> gas index from the air readings; EPD: text for the e-paper]
  -> creature_state.json (live snapshot) + SQLite history
  -> dashboard server :8080  -> static export -> VPS mirror
```

The ESP is the body. The Pi is the mind. The VPS is an observation surface only.

## The cell field: a ring of twelve

File: `Code/Python/mind/cell_field_v06.py`. Version string `v06.9-predictive`. The
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

### The battery and the reserve

Built on 7 October 2026, behind `CREATURE_BATTERY_CEILING=1` (off by default).
The body's battery sets how much the shared reserve can hold. The collector
passes the last cell voltage the body reported into `field.step(battery_v=...)`
each tick, and the same to the twin. The field smooths it over about two
minutes and reads the ceiling from a three-point curve:

| Cell voltage | Reserve's ceiling | Running time left (7 October) |
|---|---|---|
| 3.80 V and above | whole | two thirds or more |
| 3.60 V | 43 % | about 4 hours |
| 3.45 V and below | 25 %, the floor | about 1 hour |

Straight lines between the points. With the setting off, or with no reading,
the ceiling is whole. A gap in the readings keeps the last voltage for ten
minutes before the ceiling goes back to whole.

Nothing else in the field changes. The cells are still refilled in full, so they
sense and learn as before. What a lower reserve does is dim the expression,
through the decoder's energy gate (full above 40 % of the reserve's maximum, so
the reserve falls from 3.80 V but the dimming begins near 3.60 V). Since this
build the gate also scales the whole strip frame, not only arousal: arousal
carries little of the strip's light, and without that the strip lost only 13 %
of its light when arousal halved. At the floor the strip is sent about 40 % of
its usual light and arousal is about half. The voice's volume is not touched;
the amp needs its digital level kept in a clean range.

The cell reads about 0.15 V higher on the charger, so plugging it in lifts the
ceiling within a couple of minutes, and pulling the charger lowers it the same
way. Below the mind's floor the body has its own reflex: it caps the strip at
3.40 V and sleeps at 3.30 V (see "Design philosophy").

Three designs failed first, and the limits come from them:

- Scaling the refill rate did nothing until income fell under what the cells
  draw (about a third of normal). Below that everything went at once:
  expression fully dark, a sleep every four minutes, and the links wiped (mean
  weight 0.33 to 0.02 in two hours).
- A ceiling with no floor is safe down to about 20 %. Under about 18 % the
  reserve sits at the low-energy sleep threshold, the field sleeps every 240
  ticks, and that much replay rewrites its links (mean weight 0.46 to 1.0 in two
  hours). The floor of a quarter keeps the reserve above that threshold. A
  drained Creature that rests and sleeps more is still wanted, but it needs a
  sleep that does not do this, which is its own piece of work.
- The ceiling first followed the gauge's percentage and ran on the Pi that way
  for a day. On the run to flat the percentage reached 5 % half-way through the
  cell's real running time and 2 % with four hours left, so the Creature sat at
  the floor for the second half of every charge.

The gate is `python tools/field_lab_v06.py --battery`: a scripted cell (4.00 V,
3.70, 3.52, 3.35, back to 4.00) with readings 20 mV either way and a minute with
none, control against variant. It passes 9/9 on seeds 1, 2, 3 and 7. In that
gate the field is open loop and stays identical to the control tick for tick. In
the collector's own loop on the scripted body the dimmer strip is sensed, so the
field's life differs a little: after 30 minutes the largest link gap was 0.010
and there were up to 6 % fewer events, with no sleeps.

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

### Two-speed links (built, off)

Built on 3 October 2026 as the first attempt to push the memory horizon out. Each
ring link also keeps a slow weight that follows the fast one with a time constant
of about a week (`SLOW_RATE`). With `SLOW_MIX` above zero the drive between
neighbours uses a mix of the two, and decay (waking decay and sleep's weakening)
pulls the fast weight toward the slow one instead of toward the floor.
`SOFT_CEILING` scales growth, waking and replayed, by the room left under `W_MAX`:
a link keeps all of its growth up to `CEILING_KNEE` and then less, down to none at
`W_MAX`. `SLOW_LEAK` lets the slow weight itself drift toward the floor, on a slower
clock. The defaults are `SLOW_MIX = 0.0`, `SOFT_CEILING = False`, `CEILING_KNEE = 0.0`
and `SLOW_LEAK = 0.0`,
and with them the field is exactly as it was: the history gate's control runs are
unchanged to the last digit. The slow weights are kept and saved either way.

A collector run can switch a setting on from its environment, without a change to
the code: `CREATURE_SLOW_MIX`, `CREATURE_SLOW_LEAK`, `CREATURE_SOFT_CEILING` (1 for
on) and `CREATURE_CEILING_KNEE`. Unset, each is off. The twin runs the same field
code, so it lives under the same setting, and the collector prints the setting when
it starts. Given this way a setting behaves exactly as it does under the lab's
`--set`.

A setting is judged with `--history --compare` against a saved control, on seeds
1, 2, 3 and 7. It passes only if all of these hold:

- the expression horizon is at least twice the control's. This is judged on
  recorded senses over 72 hours. On the synthetic day the control itself never
  converges, so there is no horizon there to double;
- the newborn still learns: the weights its drive uses move at least 70% as far
  as the control's in the first three hours;
- fewer links are railed at the end;
- its links can still weaken: with the elder in a still room for 60 hours, the
  level each link its life built rests on (the floor, or its slow weight) is the
  floor or lower at the end than a day in;
- `--forward`, `--events`, `--feel`, `--curious` and `--voice` still pass.

Five settings were run on 3 October 2026. The last passes every check that can be
decided: `SLOW_MIX=0.25`, `SOFT_CEILING=True`, `CEILING_KNEE=1.0`,
`SLOW_LEAK=4.13e-7` (about a month). It is still off in the code. Since 08:45 on
4 October 2026 it is on in the live Creature, switched from the collector's start
command, with a fresh twin beside it. All five have the same result on the horizon: on recorded senses it
goes from 25, 25, 54 and 24 hours to more than 72, which passes on three seeds and
cannot be said on the fourth, where the control's 54 hours would need a 108-hour
test and the recording holds 72.

| | mix 0.5, ceiling over the whole range | mix 0.25, the same ceiling, leak of a month | mix 0.25, leak, no ceiling | mix 0.25, leak, knee at 1.5 | mix 0.25, leak, knee at 1.0 |
|---|---|---|---|---|---|
| newborn learning | 40 to 43%: fails | 58 to 61%: fails | 74 to 78%: passes | 74 to 78%: passes | 74 to 78%: passes |
| railed links, synthetic | 0 against 4 | 0 against 4 | 0 or 1 against 4 | 0 against 4 | 0 against 4 |
| railed links, recorded senses | 3 against 5 | 1 against 5 | 5 against 5, all at exactly `W_MAX`: fails | 5 against 5, at 1.91 to 1.97: fails | 2 against 5: passes |
| links can weaken | fails | passes | passes | passes | passes |
| other gates | `--feel` fails one check on seed 1 | all pass | all pass | all pass | all pass |

Read the pass with these in mind. It is the fifth setting tried against one
72-hour recording and one saved field, so it is fitted to them to some degree. The
railed count is taken at a fixed line (within 0.1 of `W_MAX`) that cuts through a
cluster of links: over the test the variant's count moves between 2 and 5 and the
control's between 1 and 8, with means of 2.7 and 3.9. And the newborn does learn
less, about a quarter less movement in its first three hours, mostly because its
unused links no longer fall.

What was learned:

- Without the leak a link can never weaken. Decay pulls the fast weight toward
  the slow one and the slow one only follows the fast one, so nothing pulls the
  slow weight down. In a ten-day run (three busy days, then seven in a still
  room) the slow weights fell on 0 of 108 link-days and no link dropped below the
  0.20 it was born with. With the leak they fell on 67 of 108.
- The soft ceiling as written brakes growth across the whole range, not only near
  the top: at a weight of 1.0 growth is halved. On its own it cuts a newborn's
  growth to about 78% of the control's.
- Under two-speed links a newborn's unused links no longer fall toward the floor
  in its first hours (0.85 of weight movement in the control, 0.07 in the
  variants). They rest on a slow weight that is still near the 0.20 they were
  born with. That movement is part of what the learning check counts.
- The ceiling and the learning check pull against each other. With the ceiling,
  learning fails. Without it, on recorded senses five links sit at exactly
  `W_MAX`, more firmly railed than in the control.
- With the knee at 1.5 the top links stop sitting on the rail but settle just
  under it. On recorded senses the control's five railed links are one at the top
  and four at the floor; the variant's five are all near the top and none is at
  the floor. With the knee at 1.0 the brake starts earlier and the five settle at
  1.85 to 1.94, two of them inside the line. Learning is unchanged at 78%: a
  newborn's links stay under 1.0 in its first hours, so a knee there does not
  touch them.
- The horizon itself moves as intended in every setting: after 72 hours of
  recorded senses the elder and the newborn still express about 0.10 apart
  (0.085 with the knee at 1.0), against a noise gap of 0.007. How long the
  horizon really is has not been measured: it lies beyond the recording.

## Sleep and consolidation

The field sleeps when it is under-stimulated for a while, or when memory pressure is
high, or when the energy reserve runs low. During sleep it replays its most
significant recent events, re-touching the links those events ran through, and
demotes weak unused links toward the scar floor. Each replayed event is consumed so
one spike cannot dominate. Sleep is triggered consolidation, not a clock.

v06.6 fixed a real bug here: sleep was never triggering because the quiet-pressure
threshold that armed it was unreachable in the twelve-cell body. The threshold was
corrected so consolidation can fire as designed.

v06.8 fixed a second one of the same kind: what counts as a significant event. The
old rule put a fixed threshold on raw pressure, and the weather anchor's steady
pressure crossed it on every tick. Every tick was an event, the 80-slot buffer only
ever held the last 80 seconds, and since sleep arrives after a quiet stretch, sleep
replayed the quiet run-up to itself: the field's own background.

The rule now reads surprise, the unit the predictive cell already speaks. A tick is
an event when its most surprised cell is at least three times more surprised than
the field usually is, and above a small floor. Usual is a running median, so the
rule follows the body and the room instead of going stale. Sleep and waking change
the sense levels and surprise the cells without the world doing anything, so no
events are taken while asleep or for 30 ticks after. When the buffer is full it
lets go of the least significant event, not the oldest, so it holds what stood out
since the last sleep. The old rule is still there as `EVENT_MODEL = "pressure"` for
control runs.

On 260,000 ticks of the Creature's own recorded senses, replayed from its saved
state: events fell from every tick to about 12% of ticks, the share that coincide
with a real sense change rose from 42% to 83%, and the top cell moved from the
weather anchor (56%) to the sound anchor (83%). The weather-to-speaker gap, the one
link the ring design wants weak, fell from 0.63 to the scar floor. It had been held
up by the background replay. One honest cost: with no background to replay, a long
still night adds nothing, so structure thins overnight more than it used to (about
83% of the day's differentiation held in simulation, against a figure above 100%
that the old replay was manufacturing).

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

Where the strip's colour comes from has two models, chosen by
`CREATURE_COLOUR_MODEL`. The default, `blend`, is the one above: a straight line
in RGB between a blue and an orange, placed by balance. On the body it read as
bright white. The W channel was not the cause (it sat near 9 of 255): blue and
orange are near opposites, so the middle of that line is grey-white (81, 84,
81), the field spends most of its time near the middle, and the glow, the pulse,
the shimmer and the event flash all add white.

`inner`, built on 7 October 2026, gives the whole strip one hue that the field's
reservoir pushes around the colour wheel. A fixed reading of the six reservoir
cells is taken against its own usual level; the hue turns one way while the
reading is above usual and back while it is below, at 1.5 degrees a tick for one
usual swing and never more than 4. Nothing is drawn by chance: the same life
gives the same colours. Along the strip the hue leans up to 50 degrees one way
where the warm senses are active and the other way at the cool ones. Brightness
still comes from each cell's activity. The pulse is a paler streak of the same
hue, an event flashes the opposite hue at its own place, the W channel is off
(a curiosity probe still lifts it), and the whole frame is at 0.6 of the
blend's scale. The hue is the decoder's own and is not saved: after a restart
it begins again at red.

A direct map from the reservoir's state to a hue was tried first. The six cells
mostly rise and fall together, so it gave two opposite colours with quick jumps
between them (up to 60 degrees in a tick).

The gate is `python tools/field_lab_v06.py --colour`, the same field read by
both models. It passes 9/9 on seeds 1, 2, 3 and 7: white on 0 % of lit pixels
against 24 %, all twelve twelfths of the wheel in near-equal shares against
three, 37 % of the light, and arousal, balance, tempo and the voice identical.
In the collector's own loop on the scripted body, where the strip's light
reaches the light sensor strongly, the changing colour is sensed: over 30
minutes there were 30 to 50 % more events than under the blend and the largest
link gap was 0.08, with the same tones and no sleeps.

When the voice speaks changed in v06.9. The old rule voiced a tone whenever arousal
was at or above 0.45. On the twelve-cell body that is true about half the time, so
with a 20-second minimum between tones the voice ran as a metronome: over 1.8 hours
on 2 October 2026 the live Creature sent 242 tones, 192 of them exactly at the
minimum spacing. It was the same kind of stale fixed threshold as the sleep and
event ones.

The voice now speaks when three things hold: arousal stands at least 0.20 above its
own usual level (a running median), something surprised the field in the last few
ticks, and arousal has come back down since it last spoke, so one rise gets one
tone. It never speaks while the field sleeps or in the 30 ticks after waking. The
pitch and length of the tone come from balance and tempo as before. On 260,000
ticks of the Creature's recorded senses that is about 20 tones an hour against 109,
with 1% of them at the minimum spacing against 67%. In a still room it is silent.
`CREATURE_VOICE_MARGIN` sets the 0.20; 0.30 gives about 13 an hour.
`CREATURE_VOICE_MODEL=fixed` brings the old rule back for control runs.

One consequence for the autobiography: its fourth dimension records whether the
body would voice a tone, so from v06.9 that bit is set far less often than in the
graph built so far.

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

## The loop record and forward model

Files: `Code/Python/mind/forward_model_v06.py`, the loop block in the collector,
and `Code/Python/tools/loop_probe.py`. This is the v06.7 layer, with the body's
own hearing added in v06.8. On its own it is passive: it watches, predicts and
learns. What it learns reaches the field in the next section.

The loop above only matters if the Creature can tell its own output from the room.
Three pieces make that measurable.

The loop probe measures the physical coupling. With the collector stopped, it
switches the strip between dark and known frames and reads the lux each one adds,
then sends known tones and compares the mic with the room just before. The answer
is a number: how many lux the strip adds at the sensor, how many times louder than
the room a tone is.

The loop record sets action next to return. Each tick the collector keeps what the
body emitted (the strip frame as four channel means, any tone's pitch, length and
volume) and the raw senses gathered while that frame was on the body (lux, mic
level and peak, motion). One row per tick goes to the `loop_log` table. These are
raw values, not the adaptive 0 to 1 ones, so this is also the raw history the
normalizer section asks for.

The forward model predicts the return. Each tick it predicts the change in lux
from the change in the strip frame, and the mic's rise above the room from the
tone it sent, compares that with what happened, and learns from the miss by a
delta rule. It works on changes, not levels, so the room's baseline drops out and
daylight is not mistaken for the strip. Every change in the room is split in two:
the part the Creature caused (the prediction) and the part it did not (the error).
A lamp switching on is error. Its own light, once learned, is not. The learned
weights persist alongside the field slow-state, and the live snapshot carries a
`loop` block that the dashboard shows under Loop.

From v06.8 the body listens while it speaks. `playTone` used to block the main loop
for the length of the tone, so nothing was measured while the speaker sounded. It
now reads the mic between writes to the amp and measures the stretch that is
certain to be inside the tone at the tone's own pitch, then sends one `vox` line
with that level and the room's level at the same pitch just before. The Creature
knows what pitch it played, so it can listen for exactly that and ignore the rest
of the room. The forward model's sound half uses the report when there is one. It
keeps one gain for each of eight bands of pitch across the voice's octave, because
the real speaker is far from even: measured on 2 October 2026 it peaks near 330 and
400 Hz, dips between them, and is weak below 300. Without the report it falls back
to how far the mic's peak rises after a tone, which a noisy room swamps.

It is behind `CREATURE_LOOP` (default on).

## The loop reaches the field

v06.9. Until here the forward model only watched. Now what it finds is felt.

Each tick the model also returns a `feel` value for each loop, 0 to 1: how strongly
the Creature just sensed its own output come back, weighted by how wrong it was
about it. A sensed echo always registers a little, 30% of its strength. The rest is
the share of it that was not predicted. The collector hands the two values to
`field.step(senses, loop=...)`, and they press on the two loop cells the ring was
drawn around: the voice on Speaker x Sound, the light on LED x Light. The pressure
is on the same scale as a sense at its anchor and starts a ripple the same way, so a
fully surprising echo lands like a loud sound.

Three things follow from that definition, and each is a check in the `--feel` gate.
A learned echo is felt faintly and a surprising one strongly: in simulation an
ordinary tone is felt at about 0.43, and the first tones through a covered speaker
at about 0.65. It then gets used to being covered, as the gain for that band drops.
A loop that does not physically exist is not felt at all: the light value is scaled
by how well the model has shown it can predict its own light, so the small random
weights a blind sensor leaves behind do not trickle into the cell. And it does not
run away: in the gate's run mean arousal is about 11% above the control, it voices no
more tones than the control, and it spends no more time at full arousal.

On the real body today the light loop is open, so only the voice is felt. The light
side is ready for the sensor to move: once the model can predict its own light, the
light sense is given the room alone (lux minus what the model credits to the strip)
and the strip's own light is felt at the loop cell instead.

It is behind `CREATURE_LOOP_FEEL` (default on). `LOOP_FEEL_GAIN = 0` in the field is
the control.

## Curiosity

File: `Code/Python/mind/curiosity_v06.py`. v06.9. This is the curiosity drive from
the dark-room probe, on the real body. There the probe was a number added to the
senses. Here it is something the Creature does, and what comes back is predicted by
the forward model and felt through the loop cells like any other echo.

Boredom is measured in the field's own terms: the number of ticks since its last
significant event. It is zero for the first 120, full after another 300, and back to
zero the moment anything surprises it, its own echo included.

Two probes. The light probe is a lift in white on every pixel, sized by boredom:
mostly faint, with an occasional larger burst so the predictive field cannot settle
into it. The voice probe is a short tone, kept rare: in a room where nothing else
happens it comes to roughly six probe tones an hour, never within 120 ticks of the
last tone. Its pitch comes from the band of its range it has tried least, or the one
it has recently been most wrong about, so it explores its voice instead of repeating
one note. The drive only proposes. Nothing goes out while the field sleeps, and no
tone goes out while the speaker is muted.

In simulation, alone in a still dark room (`--curious`): with no loop and no probes
nothing surprises it. With both, it tries all eight bands of its voice, comes to
explain about 70% of what it hears of itself, and each probe tone is felt. With a sensor that can see the strip it is livelier still, mean arousal about
0.30 against 0.10, and never pinned; hide the strip and the feeling fades within
fifteen minutes as the model stops crediting itself with the light.

An honest limit: on today's body the light probe comes back as nothing, because the
sensor cannot see the strip. It confirms that every time, and that is all it does
until the sensor moves. So the live effect of curiosity today is the voice probe.

It is behind `CREATURE_PROBE` (default on). `CREATURE_PROBE_VOICE=0` keeps the light
probe and drops the tone.

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
connection weights (each link's fast weight and its slow one), reservoir weights, energy, fatigue, relevance, ages, and the
predictive cells' learned predictions. It is written periodically, on clean
shutdown, and via an atexit handler, and reloaded on boot. Fast values (activation,
ripple) are deliberately not saved, so the Creature wakes calm but keeps its slow
self. A snapshot whose shape does not match the current field is refused rather than
half-applied. The autobiography is persisted alongside it and reloaded the same way,
and so is the newborn twin's field. The gas index's learned baseline is saved the
same way, in a file of its own, and is only taken back if it is under 12 hours
old. The snapshot is at version 2, which added the
slow weights. A version 1 file loads with each slow weight set to the fast one.
Code from before version 2 refuses a version 2 file and starts the field fresh, so
copy the saved state aside before going back to older code.

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
| forward | learns its own light and voice, leaves the room to the room | `--forward` | 7/7 |
| events | surprise rule against the old pressure rule; `--replay` runs both on recorded senses | `--events` | 6/6 |
| feel | the loop is felt: faintly when predicted, strongly when not, not at all with no loop | `--feel` | 7/7 |
| curious | bored in a still dark room, it probes, explores its voice, and learns it | `--curious` | 9/9 |
| voice | the fixed threshold is a metronome; the relative rule speaks rarely, when surprised; `--replay` too | `--voice` | 6/6 |
| history | measures the memory horizon: how long a newborn field needs, on the same input, to become indistinguishable from an elder; changes nothing; `--replay` with `--state` runs it on recorded senses; `--compare` judges a variant against a saved control, including whether its links can still weaken in a still room | `--history` | 4/4 |
| twin | the Creature is the same with the newborn twin beside it, in the field and in the collector's own loop; the twin measures what the history gate measures | `--twin` | 6/6 |

The ring, reservoir and readout rows were measured before the predictive cell became
the default. Under today's defaults they read 4/7, 3/4 and 1/2 (the ring row's own
command, which sets the leaky cell, still gives 6/7). That is unchanged by v06.8 and
is an open item, not a result.

Rule: no tuning change ships without a control-versus-variant run. This is the
difference between "I think it is emerging" and "here is the control run."

## The newborn twin

File: `Code/Python/mind/twin_v06.py`. The collector runs a second field beside the
real one, behind `CREATURE_TWIN` (default on). The twin is born fresh. Each tick it
gets the same four senses as the real field and nothing else: no loop, and nothing
it does reaches the body. The gap between the two is how much the real field's
longer life shows. It is the history gate's measurement, running live.

- The twin must not change the Creature. The field draws its noise from Python's
  shared random stream, so the twin keeps a stream of its own and swaps it in for
  its step. It runs after the body has its commands, and a fault in it switches it
  off rather than stopping the collector. The `--twin` gate checks this in the
  field, and again in the collector's own loop with `tools/scripted_body.py`, which
  runs the collector on a scripted body and clock so two runs can be compared line
  for line.
- Both fields are read through decoders held to the fixed expression model, so the
  gap means the same as in the history gate whatever model the strip uses.
- Its field is saved next to the real one, in `creature_field_state_twin_v06.json`,
  whenever the real field is saved. It is an ordinary field state. Delete it and
  the twin starts again as a newborn at the next restart.
- The live snapshot carries a `twin` block: its age in ticks, its ring weights, and
  the gaps in arousal, balance, tempo and the largest link difference, each smoothed
  over about an hour. One row a minute goes to the `twin_log` table: the mean gaps
  over that minute and the link gap at its end. Averaged over an hour, those rows
  are the history gate's hourly figures.
- The real field feels its own voice and light through the loop and the twin does
  not, so on a body whose loop is closed the gap settles above zero. What is left
  once the twin has caught up is what the loop adds.
- Cost on the Pi 3: about 3.5 ms a tick, measured 3 October 2026.

## Dashboard

Served from the Pi on port 8080, mirrored to the VPS for remote viewing.

- `server.py`: standard-library HTTP server. Serves the page and JSON for the live
  field state and history.
- `index.html`: an SVG view of the ring and reservoir, polling once a second. Cells
  show activation, state, and relevance; links show weight, with scarred links drawn
  as ghosts. The cell inspector is always open (it follows the most active cell
  until one is hovered or pinned). A Sensors panel shows the raw readings, with
  temperature in both C and F. The reservoir is drawn from its real wiring, which
  the snapshot carries (`reservoir.w`, `reservoir.w_in`, `reservoir.input_cells`):
  every in-between ring cell into every reservoir cell, and the sparse one-way
  links between reservoir cells as arrows, coloured by sign. Clicking any cell
  pins it, lifts its own wires, dims the rest, and lists each weight in the
  inspector with what it carries this tick.
- Page layout: one screen with no scrolling. A collapsible menu on the left, the
  field always in the middle, and one chosen section beside it: cell inspector,
  raw data (sensors, all cells, system health), learning, legend, or metabolism
  (with the loop). Clicking a cell opens the inspector. "What is Creature?" and
  the hardware photo sit below the fold. On a narrow screen the menu becomes a
  row of tabs and the page stacks. A day / night / auto switch sits at the foot
  of the menu; auto follows the device's light or dark setting. All colours,
  including the field's, are defined once per mode in the stylesheet.
- Legend: built by `setV06Legend` in `index.html` and meant to name every mark the
  field can draw: cell colours and sizes, what fill, outline, the three rings and
  the glow mean, each kind of line, how selection changes the picture, and the
  status dot and counters in the top bar. When the drawing code changes
  (`buildV06`, `renderV06`, `applySelectionV06`), the legend changes with it.
- `learning.py`: reads the slow numbers that show whether it is predicting
  better, over a window of its own ticks (6 h, 24 h or 7 d): how much of its own
  light's effect the forward model explained, how far each heard tone was from
  the predicted one, average surprise across the ring, and the weight of each
  ring link. Served at `/api/learning` and drawn as the Learning panel; the
  static export writes the 24 h window. The readout weights and each cell's own
  prediction are not logged, so they have no history yet. Under the charts one
  line reports the newborn twin from the live snapshot: its age and its gaps in
  arousal, balance and links.
- Sensor API: `/api/sensors` and `/api/sensors/<name>` serve light, sound, motion,
  temperature, and pressure as stable, versioned JSON so other apps and services
  can treat the Creature as ordinary sensors. Since v07.0 they also serve CO2,
  humidity, the CO2 sensor's temperature, the raw VOC and NOx counts and the VOC
  and NOx index. Raw values come from a `sensors` block the collector adds to
  the live snapshot; missing sensors are `null`. See `instructions.md` for the
  contract. Raw history is in the `loop_log` table, and the air readings' in
  `air_log`. The battery's charge, voltage and charge rate are served the same
  way, with their history in `power_log` and `/api/power_history`.
- Sensor panel: under Raw data on the local dashboard, hidden on the public
  mirror. One tile per sensor. The air tiles carry a line of their last hour,
  read from `/api/air_history` (a row every 20 ticks). The two gas index tiles
  show the index, a word against the room's own normal (normal, raised, high;
  "warming up" for the first 45 samples) and the raw count underneath. A change
  The battery tile shows the charge, a word (charging, discharging, steady),
  the cell voltage and charge rate, and a line of its last six hours. A change
  to `server.py` needs the dashboard service restarted; the page alone does not.
- Speaker mute: a button in the local dashboard header (hidden on the public
  mirror) toggles `POST /api/speaker {"muted": bool}`. It creates or removes a
  flag file, `creature_speaker_muted`, next to the database. The collector skips
  `VOX:` commands while it exists, so the speaker is silent but the C0 cell and
  the field keep running unchanged. Mute persists across reboots.
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
      src/main.cpp                   four senses + two emitters, the air sensors
                                     and the e-paper, ~10 Hz JSON
    Firmware/bench/                  per-sensor bring-up sketches
    Python/
      collector/collector.py         the runtime loop on the Pi
      mind/cell_field_v06.py          the ring, reservoir, predictive cell, readout
      mind/expression_v06.py          the decoder (field -> PIX/VOX)
      mind/expression_memory_v06.py   the autobiography layer
      mind/forward_model_v06.py       predicts its own light and voice
      mind/curiosity_v06.py           probes when nothing has surprised it for a while
      mind/twin_v06.py                the newborn twin: a second field that only watches
      mind/normalize.py               rolling 0..1 normalization
      mind/gas_index.py               Sensirion's VOC and NOx index, for the dashboard only
      mind/cell_field.py              the retired 111-cell field, kept for reference
      dashboard/                      server.py, index.html, static export, sync
      tools/field_lab_v06.py          offline replay and gate harness
      tools/scripted_body.py          the collector on a scripted body and clock, repeatable
      tools/fake_body.py              a live stand-in for the ESP over TCP
      tools/gas_index_check.py        checks the gas index port against Sensirion's C
      tools/expression_preview.py     renders the decoder offline
      tools/loop_probe.py             measures the light and sound loops on the body
      data/                           SQLite history + field snapshot (gitignored)
  Hardware/                          KiCad schematics, wiring, bench notes, photos
  Archive/                           earlier experiments and versions (gitignored)
```

## Design philosophy

The ESP stays simple and stable: read sensors, stream raw values, receive commands,
drive outputs. Nothing more. It does not store memory, make decisions, or interpret
meaning. It may have body-level safety reflexes, because those protect hardware.

It has one, built on 7 October 2026: the low-battery reflex. It acts on the
cell's voltage, not on the gauge's percentage, which read 2 % with four hours of
running left. When the cell stays at or under 3.40 V for ten seconds the body
caps the strip's brightness (12 in place of 40), whatever frames it is sent,
until the cell is back above 3.50 V or charging. When it stays at or under
3.30 V for thirty seconds the body darkens the strip, draws "Battery low" on
the e-paper (the panel keeps its image with no power), and deep-sleeps. Every
five minutes it wakes for a moment, reads the gauge, and sleeps again unless the
cell is charging or at 3.60 V or more; then it boots as usual and the collector
reconnects by itself. With no reading from the gauge the reflex does nothing.
The levels come from the run to flat of 7 October: about 55 minutes were left at
3.4 V and 20 at 3.3 V, and nothing stopped the discharge until the cell's own
protection near 2.4 V. Deep sleep does not cut the power. The PowerBoost, the
strip's idle current and the sensors still draw from the cell, so the reflex
buys time and does not replace the charger. `BAT:<volts>` is a bench test: for
two minutes the reflex acts on that voltage, and a test sleep is two 20 second
steps.

The Pi holds everything that changes: normalization, the field, learning,
expression, persistence. This lets the senses and the output stay stable while the
mind iterates in Python.

The body should stay dumb. The mind should stay changeable. That split is what lets
the Creature evolve without rewriting hardware.

## Known limitations

- The loop is validated in simulation, not yet on hardware. The physical light and
  sound loops are the next real test, and the result the project most needs.
- The light loop is physically open. Measured with `loop_probe` on 2 October 2026
  in a 768 lux room: no strip frame, up to all four channels at the cap, added a
  measurable amount at the BH1750 (every frame within the 1.15 lux sensor noise).
  The sensor has to move or be shielded before the Creature can see its own light.
  The sound loop was inconclusive in the same run: the room was louder than the
  tones, and the body stopped streaming while it played one.
- The sound loop is physically closed. With the v06.8 firmware flashed on 2 October
  2026, the first tones near 400 Hz came back at 11,500 to 16,500 at their own
  pitch, against 200 to 2,000 for the room at that pitch just before. A tone near
  300 Hz came back much weaker (about 3,700 against 1,100), so the small speaker
  is far louder at the top of its range than the bottom.
- The light-and-weather side stays weak by design, because slow steady signals
  produce no surprise. Only the loop keeps that side alive.
- Bias and novelty steering are not wired to the body, so the live Creature records
  its autobiography but is not yet steered by it.
- The battery reaches the Creature only as dimmer light, and only when
  `CREATURE_BATTERY_CEILING=1`. A drained Creature does not rest or sleep more,
  and learning does not slow: both first attempts at that damaged the links
  (see "The battery and the reserve"). The voice is as loud at 10 % as at full.
- A steady self-loop is as predictable as a steady room, so the predictive field
  habituates to it unless the probe stays unpredictable.
- The air sensors and the e-paper (v07.0) sit on the body but outside the
  Creature. The field does not sense the air, and nothing it does reaches the
  panel except as numbers to read. Bringing the air in would be a design step
  with its own gate.
- The body's WiFi signal measured -89 to -95 dBm on 4 October 2026, weak enough
  to drop the link. The cause (antenna placement or the 3V3 rail) is not
  established. The loops have not been re-measured on the body since the air
  sensing and the panel were added.

## Roadmap

The lesson of v06 holds: the organism becomes more alive when action returns through
the world, not when the model grows more complicated. The hardware roadmap follows
from that. Detail is in `Creature v07 Hardware Potential.md`. The name v07.0 went
to the air senses and e-paper build of 4 October 2026, which is not on this list:
they are passive inputs and a readout, added because the parts were at hand. The
steps below are still the direction.

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
