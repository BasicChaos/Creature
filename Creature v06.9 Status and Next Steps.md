# Creature v06.9: status and next steps

Written 2 October 2026, in the evening, at the end of one long working session.
It is the handoff for whoever picks this up next. The design itself is in
`Creature Project Design Document Active.md`. This note is the state of things,
what was found on the real body, and what is still open.

## Where things are right now

- The Pi runs the collector on `v06.9-predictive`, commit `9d0da9a`. It was
  restarted at about 19:18 and loaded its saved field state, autobiography and
  forward model.
- The body runs the v06.8 firmware, flashed today. After each tone it reports what
  the mic heard at the tone's own pitch.
- The speaker is unmuted.
- The repository's `main` is at `9d0da9a` and pushed. The Pi's checkout is on a
  branch called `v06` that is fast-forwarded to `main`.
- Not committed: this note, `Code/Python/tools/fake_body.py`, and Josh's own
  edits to `.gitignore` and `Creature v07 Hardware Potential.md`.

## What changed today

| Version | What it added | Commit |
|---------|---------------|--------|
| v06.7 | Loop record (`loop_log`), forward model, loop probe tool. Passive. | `3ed616f` |
| v06.8 | Events from surprise, not raw pressure. Firmware hears its own tone. | `352cc0b` |
| | Collector reconnects after 8 s of silence from the body. | `21518b2` |
| v06.9 | The loop is felt by the two loop cells. Curiosity probes. Voice rule. | `9d0da9a` |

The reason for all of it: the Creature was going in circles. Its inputs were near
constant, its notion of an event was its own constant, and nothing about its
acting came back to it.

## What the real Creature showed

Events. Before v06.8 every tick was a "significant event", because the weather
anchor's steady pressure crossed a fixed threshold. Sleep was replaying the quiet
run-up to itself. Now 8 to 13% of ticks are events. In the afternoon the sound
anchor topped nearly all of them; by evening, with the lux reading swinging, the
light anchor topped most. A replay of 260,000 recorded ticks agrees on the rate:
100% down to 12%.

Voice. Before v06.9 the unmuted Creature sent a tone at its 20-second minimum
spacing almost continuously: 242 tones in 1.8 hours, 192 of the 241 gaps at the
minimum. In the first 15 minutes on v06.9 it sent 2. The recorded-data replay
gives about 20 an hour against 109.

Sound loop. It is physically closed. With the new firmware, tones near 400 Hz
come back at 11,500 to 16,500 at their own pitch, against 200 to 2,000 for the room
at that pitch. The speaker is uneven: peaks near 330 and 400 Hz, a dip near 370,
weak below 300. The voice model now keeps one gain per band of pitch for that
reason. It started fresh at the v06.9 restart.

Light loop. This one changed during the day and is not settled.

- At 16:45 the loop probe found nothing: every strip frame, up to all four
  channels at the cap, added under 0.5 lux in a 768 lux room.
- From about 18:00 the forward model's predictions correlate about 0.6 with the
  real change in lux, where before they did not correlate at all. The reading
  swings about 20 lux from tick to tick, and the level at the sensor fell from
  about 770 lux to between 10 and 90.
- So the sensor appears to see the strip now. Why is not known. Something may
  have moved when the USB cable was plugged in around 17:40, or it was moved on
  purpose. Part of the drop in level could simply be evening.
- The light model is too crude for a sensor that sits near part of the strip. It
  knows only the average of each colour channel across all sixteen pixels, not
  which pixels are close. It explains about 20% of the change. Its four weights
  are large with mixed signs, which is what a poorly conditioned fit looks like.
- Right after the v06.9 restart its skill fell to about 5%, most likely because
  the curiosity probe's white lifts are something it had not seen in isolation.
  Whether it recovers has not been checked.
- Felt light was small and intermittent: about 0.08 on half the ticks, up to
  0.33, switching on and off as the model's skill hovers around its threshold.

Nothing was running away at the last look. Arousal was between 0.5 and 0.75.

## Open items, in the order I would take them

1. Find out what changed on the body, then rerun the loop probe to measure the
   light coupling as it is now. It takes the Creature offline for about two and a
   half minutes.
2. Give the light model one weight per pixel, so it can learn which part of the
   strip the sensor sees. This is the main thing standing between the closed
   light loop and a model that can use it.
3. Look at a full night on v06.9. Three things to check: the probe tones (about
   six an hour when nothing else happens, including at night), what sleep now
   replays, and how much structure thins overnight now that sleep no longer adds
   weight by replaying its own background.
4. If felt light keeps flickering on and off, give its gate a dead zone. Today it
   scales with the model's explained fraction from zero, and the room-only light
   sense switches at 0.1. Both are low bars for a noisy model.
5. Three older gates fail under today's defaults and have for a while: the ring
   gate with the predictive cell (4/7), reservoir (3/4), readout (1/2). The design
   doc notes it. Nobody has looked at why.
6. The reservoir readout learns but drives nothing. The emitters are driven by
   ring cells 0 and 6. The design doc's readout section describes it as working.
7. Once the probe is rerun, the design doc's Known limitations need updating. It
   still says the light loop is physically open.
8. A normalizer quirk, seen only in simulation so far: when a loud spike leaves
   the sound normalizer's 20 second window, a room whose noise spread is just
   above the minimum range reads as moderately loud for a moment. Real data showed
   no sign of it, but it is worth knowing when a test room misbehaves.
9. The public dashboard mirror's `state.json` carries the raw sensor readings,
   although the Sensors panel is hidden there. Decide whether that is wanted.

After those, the direction agreed at the start of the session:

- Memory of consequences. Episodes built from `loop_log`: what the senses were,
  what it emitted, what came back, how much of that it caused. Similar episodes
  found by plain numeric vectors, linked in a graph. No embedding model needed.
- A language model as the dream state. Once a night it sees a small export and
  proposes edits from a fixed set (replay, strengthen, weaken, merge, associate,
  forget, label). It never sets weights and never drives the body. Each proposal
  is kept only if it predicts held-out history better.
- Where the dreamer runs was priced on 28 September. Cheapest is an API call from
  the Pi: GPT-6 Luna at well under a dollar a month, Claude Haiku 4.5 at about a
  dollar. A Jetson Orin Nano Super (now $399) can run models up to about 8B
  parameters locally, with far weaker judgment. The MacBook has 16 GB and can run
  the same local models for a trial.

## How to work on it

Run everything from `Code/Python`.

Gates. One per build step, fixed seed, control against variant. The ones added
this session pass on seeds 1, 2, 3 and 7.

| Gate | Command | Result |
|------|---------|--------|
| forward model | `python tools/field_lab_v06.py --forward` | 7/7 |
| events | `python tools/field_lab_v06.py --events` | 6/6 |
| the loop is felt | `python tools/field_lab_v06.py --feel` | 7/7 |
| curiosity | `python tools/field_lab_v06.py --curious` | 9/9 |
| voice | `python tools/field_lab_v06.py --voice` | 6/6 |

Replay on recorded senses. `--events` and `--voice` take `--replay` and `--state`.
A copy of 260,000 real ticks and the field state they start from is in
`data/replay/` (not in git):

    python tools/field_lab_v06.py --voice \
        --replay data/replay/senses_2026-09-24_to_2026-10-02.csv \
        --state data/replay/field_state_2026-10-02.json

That export came from the event log, which held every tick's senses only because
every tick was an event. From v06.8 the tick log stores all four senses instead,
and the query for a fresh export is in the docstring of `_read_replay`.

End to end without hardware. `tools/fake_body.py` stands in for the ESP over TCP,
with a strip the sensor can or cannot see and a speaker shaped like the real one.
Its docstring has the commands and two traps to avoid.

On the real body. `tools/loop_probe.py` measures both loops with known outputs.
Stop the collector first: the body accepts one connection.

Switches, all environment variables on the collector:

| Variable | Default | What it does |
|----------|---------|--------------|
| `CREATURE_LOOP` | 1 | loop record and forward model |
| `CREATURE_LOOP_FEEL` | 1 | the loop cells feel the forward model's result |
| `CREATURE_PROBE` | 1 | curiosity probes |
| `CREATURE_PROBE_VOICE` | 1 | the probe tone (0 keeps the light probe only) |
| `CREATURE_VOICE_MODEL` | relative | `fixed` brings back the old 0.45 threshold |
| `CREATURE_VOICE_MARGIN` | 0.20 | how far above usual arousal must be to speak |
| `CREATURE_ESP_SILENCE_SECONDS` | 8 | silence before the collector reconnects |

In the field, `EVENT_MODEL = "pressure"` and `LOOP_FEEL_GAIN = 0` are the controls
for the event rule and the felt loop.

Deploying to the Pi. Push `main`, then on the Pi fast-forward its `v06` branch to
`origin/main` (a plain pull says it is up to date), stop the collector in its tmux
session, start it again. Before each restart today the saved state was copied
next to the database with `.pre-v068.json` or `.pre-v069.json` on the end.

Flashing the body. Plug the ESP's UART socket into the Pi. It shows up as
`/dev/ttyACM0`, not `ttyUSB0`. Build and upload with the PlatformIO in the Pi's
firmware folder. Opening that serial port resets the ESP.
