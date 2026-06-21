# Creature v06 rollout plan

Version: v06 rollout, June 15 2026. Status: process doc.

Pairs with `Creature v06.md` (the design and the 8-step build sequence). That doc
says what to build. This doc says how to roll it out without building a costly bug.

## The one rule

Change one layer at a time. Validate it. Commit it. Tag it. Then move to the next.

Never edit software, hardware wiring, and firmware in the same sitting. If two
layers move together and something breaks, you cannot tell which one did it. That
is the bug that costs a weekend.

## Order

Three layers, in this order:

1. Software (the field, in simulation). Pure Python, no hardware risk. The novel and
   risky logic lives here. Prove it in `field_lab` before any wire moves.
2. Hardware bring-up (each sensor and emitter, one at a time). Confirm every part
   functions on the bench with a minimal test sketch, before the real firmware
   touches it.
3. Firmware integration (the full body, streaming, then the loop). Only after the
   field logic is proven and every part reads clean on its own.

Software first because it is where the design is unproven and where the AI tools can
iterate freely with zero physical cost. Hardware second because a sensor that lies to
you is the worst bug to chase through firmware. Firmware last because it only has
value once both sides of it are known good.

## Phase 0: freeze a baseline (do this first, once)

Before any v06 work, lock a known-good point you can always return to.

- Commit everything currently working. The repo has uncommitted deletes and new
  files right now. Clean that up first so the baseline is real.
- Tag it: `git tag v05-last-good` then `git push --tags`.
- Confirm you can check it out clean: `git stash` any junk, `git checkout v05-last-good`,
  see it run, then `git checkout main`.

If you cannot return to a working version on demand, you have no safety net, and
every later phase is riskier than it needs to be.

## Phase 1: software (the field, in simulation)

This phase is steps 1 to 4 of the v06 build sequence, all in `field_lab.py`. No
hardware. Each step gets its own validation gate and its own tag.

Gate after every step. Do not start the next step until the current one passes.

- 1.1 Shrink to the twelve-cell ring. Gate: ring runs, the two correlated cells and
  two loop cells carry above-baseline weight, structure survives a simulated night.
  Control versus variant, fixed seed. Tag `v06-1-ring`.
- 1.2 Add the six reservoir cells. Gate: reservoir state is distinguishable across
  two different input histories, spectral radius swept and a working value chosen.
  Tag `v06-2-reservoir`.
- 1.3 Wire readout weights to the two emitters, train via delta rule. Gate: emitter
  output tracks field state more richly than a direct outer-ring connection.
  Tag `v06-3-readout`.
- 1.4 Swap the leaky integrator for the predictive cell. Gate: field no longer
  flattens to uniform under steady input. Tag `v06-4-predictive`.

End of phase 1: the brain works in simulation. Nothing physical has moved. If the
entropy test fails here, it fails cheap, and you fix it before spending on hardware.

Keep `expression_preview.py` in the loop. It already runs the map offline. Use it as
your eyes on every gate above.

## Phase 2: hardware bring-up (one part at a time)

Goal: prove every sensor and emitter functions on its own, with the smallest
possible test firmware, before the real firmware exists. A part that fails here
fails in isolation, where it is obvious. The same failure inside the full firmware
looks like a logic bug and eats hours.

Process for each part:

- Flash a minimal single-purpose sketch that does nothing but exercise that one part.
- Read the result over serial.
- Confirm against the pass criterion below.
- Only then add the next part. Never bring up two parts in one sketch.

Start with an I2C scan. Three of the four senses sit on I2C. Run an I2C scanner
sketch and confirm you see the expected addresses before testing any single device:

- BH1750 light: address 0x23 (or 0x5C if ADDR is high).
- MPU-6050 motion: address 0x68.
- BME280 weather: address 0x76 (or 0x77).

If a device does not show on the scan, it is wiring or power, not code. Fix it here.
Do not write driver code against a device the bus cannot see.

### Sensors

- Light (BH1750, I2C). Pass: lux value changes when you cover and uncover the sensor.
  Reads roughly zero in the dark, climbs under a lamp.
- Sound (INMP441, I2S). Pass: RMS rises when you clap or talk, falls in a quiet room.
  This one is I2S, not I2C, so it will not appear on the I2C scan. Test it on its own.
- Motion (MPU-6050, I2C). Pass: the single motion scalar rises when you move or tap
  the board, settles when still.
- Weather (BME280, I2C). Pass: temperature reads a plausible room value, pressure
  reads a plausible absolute value, both nonzero and stable. This is the sense that
  must stay nonzero at night, so confirm it is genuinely reading, not stuck.

### Emitters

- LED strip (SK6812 RGBW, ~16 px). Pass: a test pattern lights every pixel, all four
  channels (R, G, B, W) show correctly, no dead or wrong-colour pixels. Confirm the
  data-line resistor and strip power are as the v07 hardware section specifies before
  driving it. Watch current draw if powering from the board.
- Speaker (MAX98357A amp + speaker). Pass: a test tone plays clean, volume responds
  to a commanded level, no constant hiss or distortion at rest.

### Loop sensors (the two that close the loop)

Before integration, confirm the two loop-facing parts work as ordinary sensors first:

- The strip-facing light sensor reads the strip brightness when the strip is on.
- The speaker-facing mic reads the speaker output when it sounds.

Bench-test these as plain sensors in this phase. Do not close the loop yet. Closing
the loop is a firmware-integration step, done loose, in phase 3.

End of phase 2: every part reads clean on its own. You now trust your inputs and
outputs, so any later bug is in logic or integration, not in a lying part.

## Phase 3: firmware integration (the body, then the loop)

This is steps 5 to 8 of the build sequence. Now the proven field logic meets the
proven parts. Keep the ESP dumb: read, stream, receive commands, drive outputs.

Bring senses and emitters into the real firmware one at a time, behind flags. The
v06 design calls for `ENABLE_STRIP` and `ENABLE_VOICE` flags on the decoder. Add them
as you integrate, and use the same pattern for senses, so you can enable and disable
parts independently while you debug.

- 3.1 Stream the four senses into the collector, one sense added at a time. Gate:
  each sense shows live and sane on the dashboard before adding the next. Cross-modal
  cells differentiate. Tag `v06-5-senses`.
- 3.2 Wire the decoder into the collector behind `ENABLE_STRIP` and `ENABLE_VOICE`.
  Preview with `expression_preview.py` first, then enable on hardware. Gate: strip and
  voice render the field, body decides nothing. Tag `v06-6-expression`.
- 3.3 Close the loop, loose. Add the strip-facing light sensor and speaker-facing mic
  as inputs. Start the coupling weak, sensor slightly off the emitter's direct line.
  Gate: watch for self-sustained activity and for runaway. Tighten slowly across
  several sessions, not in one go. Tag `v06-7-loop`.
- 3.4 Run it for nights. Look for differentiation that survives and for the creature
  doing something the wiring did not predict. Tag `v06-final` when it holds.

The loop is the one place that can run away physically: light driving the sensor
driving the light. Treat 3.3 as the highest-risk step. Loose first, always. If it
screams into its own eye, back the coupling down, do not chase it in code.

## Validation gates: what "pass" means

A gate is not "it ran." A gate is a specific observable result you decided in advance.
For every step above, the gate is named. Before you start a step, write the one line
you expect to see when it works. If you cannot state it, you cannot tell pass from
fail, and you will commit a bug as a success.

For the software phase, gates are measurable in `field_lab` and on the dashboard:
field stays differentiated, two histories tell apart, creature stays active in a dull
room. For hardware, the gate is the physical response named per part. For firmware,
the gate is the live dashboard plus the preview tool agreeing.

## Git discipline

This is the cheapest insurance you have.

- Commit after every working change, not at end of day. Small commits make bisect
  fast.
- Tag every gate that passes, using the tags named above. A tag is a point you can
  return to in one command.
- Branch per phase: `v06-software`, `v06-hardware`, `v06-firmware`. Merge to `main`
  only when a phase fully passes. Main stays runnable at all times.
- When something breaks, do not debug forward. Diff against the last good tag:
  `git diff v06-3-readout`. The bug is in what changed.
- If a step goes bad, revert to the last tag and redo the step small, rather than
  patching a broken state.

### Finding a bug fast (bisect)

If a bug appears and you do not know which commit caused it, and you have a clean tag
from before it worked, use `git bisect`. It finds the exact bad commit in a handful
of checks instead of reading every change. This only works if you committed small and
often, which is the whole reason to do it.

## Rules for the AI tools

Your real risk with the tools is two of them making conflicting changes to the same
files, or one assuming context it cannot see.

- Commit to git before you let Codex or Cowork loose on a directory. Then their
  changes are always reversible in one command. Never run them on a dirty tree.
- One tool per layer per session. Do not point Codex and Cowork at the same files at
  the same time.
- GPT is paste-based and cannot see the rest of the directory. Treat its output as a
  suggestion you apply by hand, not as ground truth. It will assume things that are
  not there.
- After any AI-made change, run the gate for that layer before moving on. Do not stack
  a second AI change on an unverified first one.
- Save tool-limit credits by batching reading, not writing. Let a tool read the whole
  directory once and plan, then make one small change. A saved credit is not worth a
  four-hour bug hunt.

## The short version

Freeze a baseline. Prove the brain in simulation. Prove every part on the bench. Then
integrate firmware one part at a time, behind flags, with the loop last and loose.
Tag every gate. Commit small. One layer, one tool, one change at a time.
