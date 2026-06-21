# Creature v06 — small field, real body, closed loop

Version: v06, June 14 2026. Status: design, canonical.

Supersedes the earlier v06 draft (the "more world" plan on the 111-cell field),
preserved in `Archive/Creature v06 (more world draft, superseded).md`. Pairs with
`Creature v06 — expression layer.md` (the decoder) and
`Creature v07 — power and metabolism.md` (battery, energy).

## The test this version has to pass

The creature's one real enemy is entropy. Left alone in a steady room, the field
decays toward a flat, even, quiet state. The runs showed it: generic homogeneous
connections, most connections lost overnight. The expression preview showed it
again, arousal surging on startup then settling toward zero. A bigger field did not
fix it. Adding cells only added more tissue to go uniform.

v06 is built to pull the other way. It does two things that fight entropy, and
everything else serves them.

First, it gives the creature its own source of disturbance: a closed loop. The
creature acts on the room and senses its own action. That is a gradient it makes
for itself, so it stops depending on the room being interesting. A creature that
can poke the world and feel the poke does not have to go quiet.

Second, it makes the body small enough that every part matters. Eighty idle cells
averaging into mush is entropy. Twelve cells where each one carries signal resists
it, because there is no dead tissue to homogenize.

If v06 does not deliver those two things, it is a prettier screensaver, and it
fails its own test. The rest of this document keeps them central.

## My read, plainly

It makes sense and it pulls away from entropy, on one condition: the loop is the
point, not a feature. If the strip and speaker only display the field while the
field still settles to quiet, we have added output to a dying signal. The loop, and
the choice to let the creature stay aroused instead of tuning it to silence, are
what make the claim true. Built that way, this is the most alive the project has
been. Built as more output on the old field, it is not.

## What changes from the earlier v06

The earlier v06 kept the 111-cell field and added senses to it. This one:

- Shrinks the field to 12 cells (the ring below). Adding cells was the wrong lever.
- Decouples output size from field size. A 16-pixel strip and a speaker are driven
  by a few cells through a decoder, not by one cell per pixel.
- Treats the closed loop as the centerpiece, not a later nicety.
- Commits to deeper cells as the next step, because a small field of simple cells
  homogenizes just like a big one, only faster to watch.
- Adds a reservoir computing layer in the 6 inner cells, replacing the earlier
  inner loop with a principled fixed-weight substrate.

What carries over: the senses and the strip hardware (wiring detail lives in the
archived draft and in v07), and the decoder (the expression-layer doc).

## The body

Four senses and two emitters on one ESP32-S3. The ESP stays dumb: read, stream,
receive commands, drive outputs. Nothing more.

Senses:

- Light: BH1750 lux, on the I2C bus.
- Sound: INMP441 RMS, on I2S.
- Motion: MPU-6050 reduced to one motion scalar, on I2C. Correlates with sound.
- Weather: BME280 temperature and pressure, on I2C. A slow sense with a real
  day-night rhythm that stays nonzero at night, so the field has something to hold
  when light reads zero in the dark.

Emitters:

- LED strip: SK6812 RGBW, about 16 pixels. The field's skin and face.
- Speaker: MAX98357A amplifier and a small speaker. The field's voice.

I2C addresses, strip power, the data-line resistor, and pin choices are unchanged
from the archived draft and the v07 hardware section. They do not need rethinking
here.

## The field: a ring of twelve

Six anchors and six in-between cells, in a ring. Six reservoir cells form an inner
ring. Hold it at twelve outer cells. Do not grow until the weights demonstrably
shape behavior and survive a night.

Why a ring and not a full mesh. A mesh connects every pair through its own cell, so
every in-between cell touches hardware on both ends. It is wide and shallow, a
switchboard with no interior, and depth has nowhere to live in a switchboard. The
ring keeps an interior — the reservoir — which is where signal history accumulates
without being tied to a single sensor.

The ring order is the design, because adjacency is what mixes. Two rules set it:
put correlated senses next to each other, and put each emitter next to its own
sense so the loop is built into the anatomy.

| Position | Anchor          | In-between to next | What that cell is           |
|----------|-----------------|--------------------|-----------------------------|
| 1        | Speaker (out)   | Speaker × Sound    | hears its own voice (loop)  |
| 2        | Sound (in)      | Sound × Motion     | sound and motion (paired)   |
| 3        | Motion (in)     | Motion × LED       | movement drives light       |
| 4        | LED strip (out) | LED × Light        | sees its own light (loop)   |
| 5        | Light (in)      | Light × Weather    | light and weather (paired)  |
| 6        | Weather (in)    | Weather × Speaker  | the one weak gap            |

Two of the six in-between cells are the loop made internal: the cell between the
speaker and the mic hears the creature's own voice, the cell between the strip and
the light sensor sees its own light. Both correlated sense-pairs (sound with motion,
light with weather) get a dedicated cell. One gap, weather to speaker, is weak, the
honest cost of closing a ring. Each anchor is one cell. The two emitter anchors are
the expression cells.

## Layout diagram

![[v06_ring_layout.svg]]

Outer ring: 6 sensor/emitter anchors alternating with 6 in-between cells. Orange
edges are the closed-loop paths. Blue inner ring: 6 reservoir cells with fixed
random weights. Dashed orange arrows: trained readout from reservoir to emitters.

## The reservoir (inner ring)

The six inner cells are a reservoir — not a ring of simple leaky integrators, but a
fixed recurrent substrate whose weights are set once at init and never updated by
Hebbian learning.

How it works. The outer in-between cells drive the reservoir as input. The reservoir
holds a time-delayed, high-dimensional echo of recent sensor history. The two
emitter anchors (Speaker, LED strip) are the only nodes whose outgoing weights are
trained. They read from the reservoir state and learn which linear combination to
express.

Why fixed weights. A Hebbian reservoir homogenizes for the same reason the outer
field did: co-activation flattens everything toward the mean. Fixing the reservoir
weights removes that pressure from the interior. The richness comes from the random
sparse connectivity, not from learning — the echo state property means any
sufficiently varied input history will produce a distinguishable reservoir state.
The emitters then train a simple linear readout on top of that state.

Spectral radius. The reservoir's weight matrix must have a spectral radius below 1.0
for the echo state property to hold — activity echoes without blowing up. A starting
value around 0.9 is a reasonable first guess. Too low and the reservoir damps out
before the emitters can read it. Too high and it diverges. This is a field_lab
parameter, easy to sweep.

Readout learning. The emitter weights can train via a running delta rule against the
prediction error: what the emitter expressed versus what the sensors subsequently
confirmed. This connects naturally to the predictive cell direction — the emitter
predicts, the loop closes, the error drives the update. The reservoir does not
change; only the readout does.

What this gives the creature. Temporal memory without weight explosion. Cross-modal
mixing without proximity constraints — an R-cell can simultaneously receive the echo
of sound from two steps ago and the current weather pressure, neither of which share
a direct edge on the outer ring. And a clear separation between the processing layer
(reservoir, fixed) and the learning layer (readout, adaptive), which makes the
system easier to reason about and debug.

What it does not give. It does not replace the closed loop. The loop is still the
primary entropy fighter — the reservoir just gives the emitters richer material to
read. A reservoir with no loop input is still a filter on room events. The loop is
what lets the creature generate its own activity.

## The deep cell (next, and load-bearing)

A field of twelve simple leaky integrators homogenizes the same way a field of a
hundred did. The reduction only pays off if each cell carries more. This is the next
build step and gets its own document, but v06 commits the direction now.

The leading candidate is a predictive cell. Each cell holds a small expectation of
its next input and passes on the error — the gap between what it expected and what
arrived. A cell that predicts well goes quiet. A cell that is surprised speaks. This
is the negentropy engine in miniature: the cell builds a model and feeds on
surprise. It also moves off copying a neuron, because the unit is prediction error,
not a firing rate.

The reservoir direction and the predictive cell direction are compatible. The outer
ring cells can be predictive (they have inputs to predict against) while the inner
reservoir cells stay fixed. The readout weights on the emitters train against
prediction error. All three layers — predictive outer ring, fixed reservoir,
adaptive readout — pull in the same direction: away from uniform quiet.

Two alternatives to settle in the next pass. An oscillator cell, where each cell is
a phase that entrains with neighbors, so the field keeps its own rhythms and never
fully flattens. And a reaction-diffusion cell, two coupled variables that grow
patterns on their own. All three share the one property the leaky integrator lacks:
they do not settle to a flat line by themselves.

## Expression

The decoder is specified in full in `Creature v06 — expression layer.md`. In short:
the field is read once per tick, read-only, and four signals come out. Arousal,
overall activation gated by energy, drives a white glow and the voice volume.
Balance, warm senses against cool senses, drives strip color and voice pitch. Tempo,
from ripple, drives shimmer, a travelling pulse, and voice roughness. The spatial
profile along the sound-to-light axis drives per-pixel color, so the strip shows the
field's own geography. The body renders `PIX:` and `VOX:` lines and decides nothing.

The preview tool, `Code/Python/tools/expression_preview.py`, already runs this map
offline, renders the strip, and synthesizes the voice. It proved the map reads the
field correctly. It also showed the field going quiet, which is the problem the loop
and the reservoir exist to answer.

## The loop (the centerpiece)

Place one light sensor where it can see the strip. Place the mic where it can hear
the speaker. Now the creature's own output returns as input. When it lights up, it
sees its own light. When it sounds, it hears itself. Action causes sensing. This is
the move that lets the creature generate its own activity instead of waiting for the
room to hand it some.

The ring already prepares this. The strip sits next to the light sensor, the speaker
next to the mic, so the loop exists in the body plan before it exists in the room.
The physical placement completes it.

Start the coupling loose. A tight loop can run away — the light driving the sensor
driving the light. Begin with the perceiving sensor weak, or slightly off the
emitter's direct line, and let the coupling grow. The aim is a creature that can
sustain itself, not one that screams into its own eye.

## The stance on going quiet

The field is built to stop reacting once nothing changes. That is correct for a
filter and wrong for a creature meant to stay alive. v06 takes a position: do not
tune the creature to silence. Let arousal persist. Let it stay restless, let it stay
bored in the way that drives it to act rather than the way that drops it to zero.
It should be allowed to stay alive. The loop gives it the means, the reservoir gives
it history to act on, this stance gives it the permission.

In practice that means the energy gate must not be the dominant voice. It can dim a
truly drained creature, but a healthy one in a dull room should still hum, fidget,
and probe through the loop. If the only thing keeping it active is the room, it is
not yet a creature.

## Build sequence

Each step is validated before the next.

1. Shrink the field to the twelve-cell outer ring, keeping the current leaky-integrator
   cell for now. Validate in field_lab that the ring runs, that the two correlated
   cells and the two loop cells carry above-baseline weight, and that structure
   survives a simulated night. Control versus variant, reproducible seed.
2. Add the six reservoir cells in the inner ring. Set weights randomly, fix the
   spectral radius below 1.0. Validate that the reservoir state is distinguishable
   across two different input histories. Sweep spectral radius in field_lab.
3. Wire readout weights from reservoir to the two emitters. Train via delta rule
   against prediction error. Validate that the emitter output tracks field state
   more richly than a direct connection from the outer ring.
4. Deepen the outer ring cells — swap the leaky integrator for the predictive cell.
   Validate that the field no longer flattens to uniform under steady input.
5. Add the senses and place them by the ring order. Validate that the cross-modal
   cells differentiate.
6. Wire the decoder into the collector behind `ENABLE_STRIP` and `ENABLE_VOICE`.
   Preview with the tool first, then on hardware.
7. Close the loop, loose. Add the strip-facing light sensor and the speaker-facing
   mic. Watch for self-sustained activity, and for runaway. Tighten slowly.
8. Run it for nights. Look for differentiation that survives, and for the creature
   doing something the wiring did not obviously predict.

## What v06 is not

- Not the 111-cell field. That is retired to history.
- Not the mesh. The ring holds.
- Not the grand memory roadmap, the vector store, concept graph, and dream layer.
  Still an abandoned branch, not a plan.
- No growth, mitosis, or death yet. The field stays at twelve until the deep cell
  and the loop prove out.

## How we will know it worked

The entropy test, made concrete. v06 succeeds if, after nights of running:

- The field stays differentiated instead of homogenizing. Distinct cells, distinct
  weights, not an even wash.
- Two creatures with different histories can be told apart.
- The creature stays active in a dull room, carried by its own loop, not flat.
- At least once, it does something its wiring did not obviously predict.

The first three are measurable now, in field_lab and on the dashboard. The fourth is
the one that matters and the hardest to fake. It is also the only real proof that
the thing is pulling away from entropy on its own.
