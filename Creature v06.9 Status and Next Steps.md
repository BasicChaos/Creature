# Creature v06.9: status and next steps

Written 2 October 2026, updated 3 October for the dashboard work and 4 October
for the memory-horizon work. It is the handoff for whoever picks this up next. The
design itself is in `Creature Project Design Document Active.md`. This note is the
state of things, what was found, and what is still open. The findings on the real
body further down are still those of 2 October.

## Where things are right now (4 October 2026, 09:00)

- The Pi runs the collector on commit `2d889b2`, restarted at 08:45 on 4 October.
  `main` is pushed to the same commit.
- **Two-speed links are switched on in the live Creature**, from the collector's
  start command, not in the code. A plain restart turns them off. The start
  command is under "Deploying to the Pi" below. Check what is in force with the
  `Two-speed links:` line the collector prints at start, or in
  `/proc/<pid>/environ`.
- The relative expression model is also on, the same way
  (`CREATURE_EXPRESSION_MODEL=relative`).
- A newborn twin runs beside the real field. The present twin was born at the
  08:45 restart (real tick 932373) and lives under two-speed links too.
- The saved field is now snapshot version 2. Code from before commit `93b3270`
  refuses it and would start the field fresh. State from before the switch is
  next to the database as `*.pre-twospeed.json` (version 1), and from before the
  twin as `*.pre-twin.json`.
- The body runs the v06.8 firmware. It runs on its battery when the power cable
  is out: on 4 October the battery ran flat at 07:08 and the body was off the
  network until 08:40. It needed the cable and a press of reset. The collector
  kept retrying and reconnected by itself; nothing was restarted for that.
- The speaker is unmuted.
- Not committed: this note's 3 and 4 October updates, the same in `CLAUDE.md`,
  a legend paragraph in the design doc, and `Claude Code brief - memory
  horizon.md`. Check `git status`.

## The memory-horizon work (3 and 4 October)

The brief is `Claude Code brief - memory horizon.md`. Its three steps are done.
The numbers and the reasoning are in the design doc, under "Two-speed links",
"The newborn twin" and the gate table.

The problem. The Creature learns, but a newborn field fed the same recorded day
becomes indistinguishable from the ten-day-old one in about a day. The memory
horizon is how long that takes. It is the number any change to the mind is now
judged by.

| Step | What it added | Commit |
|------|---------------|--------|
| 1 | The history gate (`--history`): measures the horizon, changes nothing | `d6634f4` |
| 2 | The newborn twin in the collector, the `--twin` gate, `tools/scripted_body.py` | `8ef5533` |
| 3 | Two-speed links, off by default, snapshot version 2 | `93b3270` |
| | Slow leak, ceiling knee, a stricter `--compare` verdict | `2abb17f` |
| | Environment switches for the two-speed setting | `2d889b2` |

What was found.

- Today's mind, on recorded senses: horizon 24 to 26 hours. It needs a 72-hour
  test window (`--history-hours 72`); at 24 hours the answer flips with the seed.
- On the synthetic day the control never converges, so the horizon is judged on
  recorded senses only.
- Two-speed links as first written could never forget: nothing pulled the slow
  weight down. The leak fixed that. The ceiling as first written braked growth
  over the whole range; the knee fixed that.
- The setting that passes the gate, and that is live now: `SLOW_MIX=0.25`,
  `SOFT_CEILING=True`, `CEILING_KNEE=1.0`, `SLOW_LEAK=4.13e-7`. The horizon goes
  from about a day to more than 72 hours, the longest the recording can show. It
  is the fifth setting tried on one recording, so it is fitted to it to some
  degree, and the newborn learns about a quarter less in its first hours.
- The live baseline, under today's mind: the first twin lived 15 hours 54
  minutes (3 October 15:19 to 4 October 08:45, less the battery gap). Eight of
  twelve links came within 0.09 of the real field's. What stayed apart was
  around cell 7, the LED-and-light loop cell: the real field feels the loop and
  the twin does not. The expression gap settled at 0.05 to 0.07. That twin's
  field is kept as `creature_field_state_twin_v06.baseline.json`.
- So the twin's single "largest link gap" figure measures the loop once the
  twin has caught up, not history.
- After a collector restart the field marks almost no events, and so voices
  nothing, for 5 to 10 minutes. Seen at three restarts. The cause is not
  established.

Open, in the order I would take them.

1. Read the two-speed twin against the baseline at the same ages. Rows are in
   the `twin_log` table, one a minute; the two twins are told apart by
   `twin_age` starting again from zero at tick 932373. Under two-speed links the
   gap should still be clearly open at 16 and at 24 hours.
2. Log each of the twin's link weights once a minute, so the gap can be read on
   the links away from the two loop cells (1 and 7). Offered, not built. Without
   it the comparison in item 1 is hard to read.
3. Find out why events stop for some minutes after a restart. One candidate: the
   event rule's running median (`event_usual`) is not saved.
4. `SLOW_RATE` (the week) has no environment switch and has never been run at
   another value. It is the knob that sets how long the memory lasts.
5. The older open items below still stand.

## What changed on 2 and 3 October

| Version | What it added | Commit |
|---------|---------------|--------|
| v06.7 | Loop record (`loop_log`), forward model, loop probe tool. Passive. | `3ed616f` |
| v06.8 | Events from surprise, not raw pressure. Firmware hears its own tone. | `352cc0b` |
| | Collector reconnects after 8 s of silence from the body. | `21518b2` |
| v06.9 | The loop is felt by the two loop cells. Curiosity probes. Voice rule. | `9d0da9a` |

After v06.9, on 2 and 3 October, the dashboard was reworked. None of it touches
the Creature's behaviour.

| What | Commit |
|------|--------|
| The reservoir drawn from its real wiring; click a cell to see its connections | `12b24de` |
| Fixed panel sizes, compact inspector | `a376349` |
| Learning panel: own light, own voice, surprise, ring links over time | `fd8770b` |
| Static export refreshes `learning.json` every five minutes | `fe1739c` |
| Left menu, field always visible, one section beside it | `cc68ec8` |
| Public mirror goes stale after 20 s, not 8 | `eeeee5e` |
| Day mode, with a day / night / auto switch | `1b6c2fd` |
| Legend rewritten to cover every mark the field draws | `e300d14` |

The legend had said the loop "isn't physically closed yet". It now says the sound
loop is closed and the light loop is not settled. When open item 1 settles the
light loop, change that sentence in `setV06Legend` in `dashboard/index.html`.

The reason for the v06.7 to v06.9 work: the Creature was going in circles. Its inputs were near
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

## Older open items, in the order I would take them

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
   still says the light loop is physically open. The dashboard legend's loop
   sentence needs the same update.
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
| history | `python tools/field_lab_v06.py --history` | 4/4 |
| twin | `python tools/field_lab_v06.py --twin` | 6/6 |

The history gate judges a variant against a saved control. On recorded senses,
with the setting that is live now:

    python tools/field_lab_v06.py --history --seed 1 --history-hours 72 \
        --replay data/replay/senses_2026-09-24_to_2026-10-02.csv \
        --state data/replay/field_state_2026-10-02.json --json control.json
    python tools/field_lab_v06.py --history --seed 1 --history-hours 72 \
        --replay data/replay/senses_2026-09-24_to_2026-10-02.csv \
        --state data/replay/field_state_2026-10-02.json \
        --set SLOW_MIX=0.25 --set SOFT_CEILING=True --set CEILING_KNEE=1.0 \
        --set SLOW_LEAK=4.13e-7 --compare control.json

Each takes several minutes. `--express` reads 8/9 at defaults and did before
this work.

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
`tools/scripted_body.py` runs the real collector loop on a scripted body and a
pretend clock, so a run comes out the same every time and two runs can be
compared line for line. `--twin-check` uses that to show the twin changes nothing.

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
| `CREATURE_EXPRESSION_MODEL` | fixed | `relative` scales the strip and tone against the field's own usual; on at the Pi |
| `CREATURE_TWIN` | 1 | the newborn twin |
| `CREATURE_SLOW_MIX` | 0.0 | two-speed links: share of the drive taken from the slow weight; 0.25 at the Pi |
| `CREATURE_SOFT_CEILING` | 0 | 1 scales growth by the room left under the ceiling; 1 at the Pi |
| `CREATURE_CEILING_KNEE` | 0.0 | weight up to which growth is not braked; 1.0 at the Pi |
| `CREATURE_SLOW_LEAK` | 0.0 | the slow weight's drift toward the floor, per tick; 4.13e-7 at the Pi |

In the field, `EVENT_MODEL = "pressure"` and `LOOP_FEEL_GAIN = 0` are the controls
for the event rule and the felt loop.

Deploying to the Pi. Push `main`, then on the Pi fast-forward its `v06` branch to
`origin/main` (a plain pull says it is up to date), stop the collector in its tmux
session, start it again. Before each restart the saved state was copied next to
the database with a name such as `.pre-twospeed.json` on the end.

The start command in force since 4 October, typed in the tmux pane (in
`~/Creature/Code/Python`, venv active). Leave any part out and that part is off:

    CREATURE_EXPRESSION_MODEL=relative CREATURE_SLOW_MIX=0.25 \
    CREATURE_SOFT_CEILING=1 CREATURE_CEILING_KNEE=1.0 CREATURE_SLOW_LEAK=4.13e-7 \
    python collector/collector.py tcp://creature-esp.local:7777

To start a fresh twin, stop the collector first, then move
`creature_field_state_twin_v06.json` aside, then start. The collector saves the
twin again as it exits, so moving the file while it runs does nothing. Wait ten
minutes after a restart before judging it by events or tones.

Looking at a dashboard change. Start the `creature-dashboard-test` entry in
`.claude/launch.json` and open http://localhost:8791. It reads a snapshot from a
scratch folder; if the field is empty, copy `dashboard/public/state.json` there.
The page says "stale" because the snapshot is old. On the Pi the dashboard serves
`index.html` from disk, so a fast-forward is the whole deploy, and the sync loop
carries the page to the public mirror within a minute.

Flashing the body. Plug the ESP's UART socket into the Pi. It shows up as
`/dev/ttyACM0`, not `ttyUSB0`. Build and upload with the PlatformIO in the Pi's
firmware folder. Opening that serial port resets the ESP.
