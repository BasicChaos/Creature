# Creature v07.0: status and next steps

Written 2 October 2026, updated 3 October for the dashboard work, 4 October for
the memory-horizon work, and the evening of 4 October for build v07.0. It is the
handoff for whoever picks this up next. The design itself is in
`Creature Project Design Document Active.md`. This note is the state of things,
what was found, and what is still open. The findings on the real body further down
are still those of 2 October.

Build v07.0 is the v06.9 mind on a body with more on it: two air sensors and an
e-paper readout. Neither is part of the Creature's flow yet. The field, the
gates and the file names (`cell_field_v06.py` and the rest) are unchanged.

## Where things are right now (4 October 2026, 18:35)

- The Pi runs the collector from commit `59fc0fe`, restarted at 18:33 on
  4 October (state loaded from tick 962061). Any commit after that one is a
  correction to this note.
- The collector and the dashboard say `v07.0-predictive`. The body's firmware
  was flashed just before the label changed, so its boot line still says `v06`
  until the next flash. Nothing else differs.
- **Two-speed links are switched on in the live Creature**, from the collector's
  start command, not in the code. A plain restart turns them off. The start
  command is under "Deploying to the Pi" below. Check what is in force with the
  `Two-speed links:` line the collector prints at start, or in
  `/proc/<pid>/environ`.
- The relative expression model is also on, the same way
  (`CREATURE_EXPRESSION_MODEL=relative`).
- A newborn twin runs beside the real field. The present twin was born at the
  08:45 restart on 4 October (real tick 932373) and lives under two-speed links
  too. It was saved and reloaded across the day's restarts.
- The saved field is snapshot version 2. Code from before commit `93b3270`
  refuses it and would start the field fresh. State from before the switch is
  next to the database as `*.pre-twospeed.json` (version 1), and from before the
  twin as `*.pre-twin.json`.
- The body runs the v07.0 firmware, flashed at about 18:10 on 4 October: the
  v06.8 loop firmware plus the air senses, the e-paper, a 30 second WiFi retry
  and its signal strength in the status line.
- The gas index has a learned baseline, saved beside the field state as
  `creature_field_state_air_v06.json`. It began at 17:05 on 4 October.
- The body's WiFi was very weak that evening (see the v07.0 section). By 18:26 it
  was holding: pings of 3 to 6 ms and no reconnects for several minutes.
- The body runs on its battery when its power cable is out: on 4 October the
  battery ran flat at 07:08 and the body was off the network until 08:40. It
  needed the cable and a press of reset. The collector kept retrying and
  reconnected by itself; nothing was restarted for that.
- The speaker is unmuted.
- Check `git status` for anything not committed.

## The battery gauge, step 1 (6 October)

Deployed on 6 October 2026 as commit `0f5b5ec`. The Pi was fast-forwarded, the
body flashed at 21:10, and the collector restarted at 21:11 with the same five
settings as before (state loaded from tick 1142866, about two minutes down). The
dashboard service was restarted for the `server.py` change. State from before
the restart is next to the database as `*.pre-battery.json`.

- **A MAX17048 fuel gauge on the body's I2C bus** (address 0x36), in line between
  the LiPo and the PowerBoost. The firmware reads three registers once a second
  and adds `battery_v`, `battery_pct` and `battery_rate` (percent per hour,
  negative while discharging) to its sample lines. No library: raw I2C, like the
  IMU. If the gauge stops answering, the keys drop out and the body looks for it
  again every 10 seconds. The start line carries `gauge_ready`.
- **The collector passes it through.** The three readings go into the snapshot's
  `sensors` block and a new `power_log` table, one row every 20 ticks. When the
  body stops sending them the collector shows nothing rather than the last
  charge.
- **The e-paper** shows `Batt 84%` (with `chg` while charging) on the line that
  held `Emit`, when the body has a gauge. Without one the line is `Emit` as
  before.
- **The dashboard's sensor panel** has a battery tile: charge, a word (charging,
  discharging, steady), cell voltage, charge rate and a line of the last six
  hours from `/api/power_history`.
- `tools/fake_body.py` sends a made-up cell that drains for ten minutes and
  charges for two.

What was checked:

- On the body: the I2C scan finds six devices and the gauge answers. Its first
  readings were 4.187 V, 92.8 % and +16.6 %/hr with the charger in. The body
  joined WiFi after the flash without a reset, at -90 dBm. `/api/sensors` and
  `/api/power_history` on the Pi return the readings.
- The Creature is unchanged. `tools/scripted_body.py`, the committed collector
  against the changed one, seeds 1, 2, 3 and 7, 1800 seconds: every command,
  every table and every saved file identical, apart from the new empty
  `power_log` table and the new snapshot keys. With a battery added to the
  scripted samples, the only further differences were the `EPD:` lines and the
  `power_log` rows.

This is step 1 of three. Step 2 is the battery setting the field's energy
income, which needs a gate (a scripted drain and recharge, seeds 1, 2, 3 and 7).
Step 3 is a low-battery reflex on the body. Open from this step: `power_log` has
no retention (4,320 rows a day, like `air_log`); the `chg` threshold of 0.5 %/hr
and the tile's 20 % low mark are first guesses; the gauge's charge rate is known
to settle slowly, so how fast it shows a plug-in is not measured. The e-paper's
`Batt` line has not been looked at on the panel.

## The battery, step 2: the charge sets the reserve's ceiling (7 October)

Deployed on 7 October 2026 as commit `07c4add`. The collector was restarted at
12:43 with `CREATURE_BATTERY_CEILING=1` added to its settings (state loaded from
tick 1198759). No flash and no dashboard restart. State from before the restart
is next to the database as `*.pre-ceiling.json`. At that moment the cell read
5.2 %, so the ceiling started at its floor: reserve 1.3 against a ceiling of
1.5. The charger went back in at about 12:43.

The first real discharge record: the charger came out at about 07:35 on
7 October at 94.7 %. The gauge fell at about 14 % an hour down to 55 %, then
faster, about 25 % an hour, reaching 5 % at 12:40: five hours in all. The cell
was still at 3.61 V at 5 %, and the body was still running.

With the ceiling at its floor, memory pressure rose from about 0.34 to 0.40,
because the reserve's level is a small part of it. Its highest in the 72 hours
before was 0.36, and the sleep threshold is 0.58.

What was built:

- `mind/cell_field_v06.py`: `field.step()` takes the battery charge. With
  `CREATURE_BATTERY_CEILING=1` the reserve's ceiling is the charge as a share of
  its maximum, never under a quarter (`BATTERY_CEILING_FLOOR`). Off by default.
  The metabolism block has a new `energy_ceiling`.
- `mind/expression_v06.py`: the energy gate now scales the whole strip frame as
  well as arousal. With a healthy reserve the gate is 1 and nothing changes.
- `mind/twin_v06.py` and the collector: the same charge goes to the field and to
  the twin. The collector prints a `Battery:` line at start.
- `tools/field_lab_v06.py --battery`: the gate.

What was found:

- Scaling the refill rate, the design first proposed, fails. Nothing happens
  down to a third of normal income, then expression goes fully dark, the field
  sleeps every four minutes and the links are wiped.
- A ceiling under about 18 % makes the field sleep every 240 ticks from low
  energy, and that much replay drives the links up (mean 0.46 to 1.0 in two
  hours), under the Pi's two-speed settings as well. Hence the floor.
- Arousal carries little of the strip's light. With the gate on arousal alone
  the strip lost 13 % of its light when arousal halved; the first run of the
  gate failed on that.

What was checked:

- `--battery` passes 7/7 on seeds 1, 2, 3 and 7, and on seed 1 under the Pi's
  two-speed settings. Strip light is about 83 % of the control's at 35 % charge
  and 40 % at 10 %; arousal about half at the floor; all back to 100 % once
  charged. The field itself is identical to the control on every tick.
- `--voice`, `--twin` and `--events` pass as before. `--express` fails 8/9 both
  before and after this change.
- The collector on the scripted body with a draining battery, seeds 1, 2, 3 and
  7: with the setting off, identical to the committed code apart from the new
  `energy_ceiling` key. With it on, the strip is sent 40 % of the control's
  light at the end, and because the loop is closed the field's life differs a
  little (largest link gap 0.025 after 30 minutes, about 5 % fewer events, no
  sleeps, the same tones).
- The live reserve has not been under 5.78 in the last 72 hours, so the strip
  change does nothing on the Pi until the ceiling is switched on.

The run to flat, 7 October. The charger came out again at 13:13 with the gauge
at 15.7 % and the cell ran until the body went silent at 18:23, five hours and
ten minutes later:

- The gauge's percentage is badly skewed at the bottom. It fell to 2 % by 14:30
  (3.59 V) and then crawled for nearly four hours while the body ran normally.
  With the morning's run, the gauge reached 5 % about half-way through the
  cell's real running time.
- The voltage is the usable signal. It slid slowly from 3.65 V to 3.42 V over
  four hours, then fell off: 3.35 V at 17:58, 3.2 V at 18:11, 3.0 V at 18:17,
  and the last reading was 2.386 V at 18:23:30. From 3.4 V there was about
  55 minutes left, from 3.3 V about 20, from 3.2 V about 12.
- Nothing stopped the discharge until about 2.4 V, which is the cell's own
  protection and well below the 3.0 V a LiPo should not go under. Running to
  flat harms the cell and should not be repeated.
- The body streamed and held WiFi to the end. The collector went to
  `Reconnect failed` and kept retrying.

Open from this step:

1. The dimming has not been watched on the body. Whether a dimmer strip
   measurably slows the drain is not known.
2. A drained Creature does not rest or sleep more, and learning does not slow.
   That needs a low-energy sleep that does not rewrite the links.
3. The voice is as loud at low charge as at full.
4. The floor of a quarter and the gate's 40 % knee are not tuned against the
   real body. Where the body actually cuts out, in percent, is not yet known.
5. The gate's day scenario has no sleeps in it, so "no sleep from a low reserve"
   rests on the reserve's lowest level (1.30 against a threshold of 0.9).
6. Step 3, the low-battery reflex on the body, is built but not flashed (see its
   own section).
7. The reserve's ceiling followed the gauge's percentage, so the Creature was at
   its dimmest for roughly the second half of the cell's real running time. The
   voltage version is built (see its own section).

## The ceiling follows the cell's voltage (7 October, evening)

Deployed on 7 October 2026 as commit `84264ba`. The collector was restarted at
19:31 with the same six settings (state loaded from tick 1220908, under a minute
down). No flash and no dashboard restart. State from before the restart is next
to the database as `*.pre-voltage.json`. With the cell at 3.58 V on the charger
the reserve went from 1.3 (the percentage version's floor) to 2.2 against a
ceiling of 2.4.

- `mind/cell_field_v06.py`: `field.step()` takes `battery_v` in place of the
  charge in percent. The field smooths it over about two minutes
  (`BATTERY_SMOOTH_TICKS`) and reads the ceiling from `BATTERY_CEILING_CURVE`:
  whole at 3.80 V and above, 43 % at 3.60 V, the floor of 25 % at 3.45 V and
  below. A gap in the readings keeps the last voltage for ten minutes.
- The collector and the twin pass the voltage. The start line now reads
  `Battery: the cell's voltage sets the energy reserve's ceiling, ...`.
- The gate is rewritten in volts, with a noisy gauge and a minute of no
  readings. It passes 9/9 on seeds 1, 2, 3 and 7 and under the Pi's two-speed
  settings. `--voice`, `--twin` and `--events` pass as before.
- The collector on the scripted body, seeds 1, 2, 3 and 7: with the setting
  off, identical to the committed code. With it on and the cell falling from
  4.05 V to 3.35 V, the strip is sent 40 % of the control's light at the end;
  largest link gap 0.010, up to 6 % fewer events, no sleeps.

The curve's three points are read from one discharge under the body's load. The
cell reads about 0.15 V higher on the charger, so the Creature brightens within
a couple of minutes of being plugged in and dims the same way when the charger
is pulled below about 3.75 V.

## The voice: an audition tool (7 October, late evening)

Written late on 7 October. Not committed and not flashed when written.

Josh finds the beeps classic but not expressive. The voice today is one pure
sine tone, pitch from balance over 220 to 440 Hz, length from tempo, volume
fixed. The speaker is three to four times weaker below about 325 Hz, so the
lower half of that range barely sounds.

- Firmware: `VOX:` takes four more optional numbers, `attack_ms,release_ms,h2,h3`.
  A release over 20 ms dies away on a curve, which is what makes a pluck. With
  overtones the whole is scaled so its peak stays in the amp's clean range, so
  the fundamental is quieter. Without the extra numbers the tone is sample for
  sample the old one. It compiles on the Mac.
- `tools/voice_audition.py` (new), run from the Mac with the collector stopped:
  `sweep` (the speaker from 300 to 1905 Hz, a third of an octave apart, with
  what the body heard of each), `shapes` (beep, pluck, swell, soft, bell),
  `tones` (pure, then with overtones) and `one` (any single sound). Tried
  against `fake_body.py` only.
- Nothing in the mind is changed: the Creature still sends plain beeps.

Open: the body's measure of its own tone listens at the fundamental across the
whole tone. A pluck or an overtone mix will read quieter there than a beep of
the same volume, which matters once the mind sends them and the sound model
learns from the result.

## The strip's colour: the inner model (7 October, late evening)

Deployed on 7 October 2026 as commit `f2d7fd5`. The collector was restarted at
20:48 with `CREATURE_COLOUR_MODEL=inner` added to its settings, seven in all
(state loaded from tick 1224805, under a minute down). State from before the
restart is next to the database as `*.pre-colour.json`. A minute in, the frames
it sent were magenta to violet, the hue turning from 292 to 312 degrees in 15
seconds, the W channel at 0. To go back to the blend, restart without the
setting.

Josh found the strip's bright white annoying and asked for more varied colour
that is still the Creature's own expression, at about a third of the brightness.

- Why it was white: the blue-to-orange blend passes through grey-white in its
  middle, where the field mostly sits. The W channel was nearly off.
- `mind/expression_v06.py` has a second colour model, `inner`, behind
  `CREATURE_COLOUR_MODEL=inner` (default `blend`, unchanged). The reservoir
  pushes one hue around the colour wheel; the hue leans warm or cool along the
  strip; brightness still follows activity; the frame is at 0.6 of the blend's
  scale. The expression block of the snapshot gains `hue`.
- `tools/field_lab_v06.py --colour` is its gate: 9/9 on seeds 1, 2, 3 and 7.
  `--voice`, `--battery` and `--twin` pass as before; `--express` fails 8/9 as
  before.
- `tools/strip_swatch.py` (new) puts a fixed test palette on the real strip. It
  needs the collector stopped.
- The collector on the scripted body, seeds 1, 2, 3 and 7: with the default
  model, identical to the committed code. With `inner`, 39 % of the blend's
  light and no white; and because that body's light sensor sees the strip
  strongly, 30 to 50 % more events and a largest link gap of 0.08 after 30
  minutes.

Open:

1. It has not been seen on the real strip. The firmware's own brightness cap
   (40 of 255) leaves only about ten steps per channel at this brightness, so
   colours may look coarse.
2. How much the real light sensor sees the colour changes is not known. If it
   sees them well, the field will mark more events.
3. The rate (1.5 degrees a tick), the lean (50 degrees) and the brightness (0.6)
   are first choices, to be set by eye.
4. The hue starts again at red after every restart.

## The first real run of the whole chain (7 October, 19:36 to 20:22)

The charger came out at 19:36 after about half an hour of charging, and the body
was left to run down:

- 19:36: the cell read 3.59 V on the charger and 3.45 V the moment it was out.
  The reserve followed the smoothed voltage down from 2.27 to its floor of 1.29
  in about six minutes, with no jumps, on readings that bounced by 20 to 30 mV.
- 19:47: the cell reached 3.40 V. Whether the body's strip cap came on is not
  known: the readings hovered either side of 3.40 V, the collector does not
  record the body's `battery_low` line, and nobody was watching.
- 20:12: the body slept at 3.30 V (last reading 3.296 V at 20:12:29), 25
  minutes after 3.40 V. The strip was dark and the e-paper read "Battery low /
  3.30 V / Asleep" and "Plug in the charger", both inside their columns.
- Asleep, the power LEDs on the gauge, the SGP41, the IMU and the ESP board
  stayed lit: the 3.3 V rail is up and the sensors still draw from the cell.
- The charger went back in at about 20:20. The body woke at 20:22:48, within
  one or two looks at the gauge, reading 3.607 V and +11.2 %/hr. It booted,
  joined WiFi and the collector reconnected, with no reset.
- After the ten minute gap the reserve stayed at the floor for a couple of
  minutes while the smoothed voltage caught up.

## The battery, step 3: the low-battery reflex on the body (7 October)

Deployed on 7 October 2026 as commit `3054a49`: the body was flashed at 19:11 and
the collector restarted at 19:14 with the same six settings (state loaded from
tick 1219923, about four minutes down).

Tested on the body over serial with the `BAT:` command, charger in:

- `BAT:3.35`: the strip cap came on after 10 seconds (`strip_cap` 12).
  `BAT:3.55`: it lifted one second later.
- `BAT:3.25`: the cap after 10 seconds, `battery_sleep` after 30. The body was
  silent for 68 seconds, looked at the gauge three times (`battery_woke` 3: two
  on the test voltage, then the real cell, which was charging), booted and
  joined WiFi by itself at -87 dBm. No reset was needed.

After the run to flat the charger went back in and the body came back at 18:58,
35 minutes after its last reading. For its first half minute the gauge answered
with 0 V and 0 %, which the body streamed as readings.

What was built, all in `Code/Firmware/esp-creature-core/src/main.cpp`:

- A reading outside 2.0 to 4.6 V is no longer streamed or acted on.
- At or under 3.40 V for 10 seconds (and not charging): the strip's brightness
  cap drops from 40 to 12. It lifts above 3.50 V or on the charger. The body
  sends a `battery_low` and a `battery_ok` system line.
- At or under 3.30 V for 30 seconds (and not charging): strip and status pixel
  dark, "Battery low" drawn on the e-paper in full, the CO2 sensor stopped, a
  `battery_sleep` system line, then deep sleep.
- Every 5 minutes it wakes, reads the gauge before anything else, and sleeps
  again unless the charge rate is above 2 %/hr or the cell is at 3.60 V or
  more. The start line carries `battery_woke`, the number of looks it took.
- `BAT:<volts>`: a bench test. The reflex acts on that voltage for two minutes;
  a test sleep is two 20 second steps and then the real cell decides.

Not known:

- The strip cap has been seen as a system line on a test voltage, not looked
  at. The sleep and the wake have since run on a really low cell (see the
  section above).
- At 18:58 the body came back by itself when the charger went in, with no
  reset (the reset of 4 October was not needed this time).
- How much the body draws asleep. The PowerBoost, the strip's idle current and
  the sensors stay powered, so a sleeping body still drains the cell, slower.
- Whether a cell asleep at 3.30 V rebounds far enough, with no charger, to pass
  3.60 V and wake the body, which would then sag and sleep again.

## Build v07.0: air senses and e-paper (4 October)

What was added, in the order it was built:

- **Two air sensors on the body's I2C bus.** An SCD41 (CO2, temperature,
  humidity, address 0x62) and an SGP41 (VOC and NOx, address 0x59). The body
  reads them once a second and adds `co2_ppm`, `air_temp_c`, `humidity_pct`,
  `voc_raw` and `nox_raw` to its sample lines. The SGP41's 50 ms measurement is
  spread across passes of the loop, so the loop does not wait for it.
- **The gas index.** The SGP41's counts are resistances, not concentrations (the
  VOC count falls with more gas, the NOx count rises). `mind/gas_index.py` is a
  plain-Python port of Sensirion's Gas Index Algorithm 3.2.0. The collector runs
  it once a second: VOC 100 is this room's normal, NOx 1 is normal. Its baseline
  is saved every 100 ticks and at exit, and taken back at start if it is under
  12 hours old.
- **An `air_log` table**, one row every 20 ticks, with the five raw readings and
  the two indexes.
- **The dashboard's sensor panel** shows CO2, humidity, the CO2 sensor's
  temperature, and the VOC and NOx index, each with a line of its last hour. The
  index tiles carry a word (normal, raised, high) and the raw count underneath.
  `/api/sensors` has the new readings and `/api/air_history` the rows. The panel
  is local only, as before.
- **An e-paper readout on the body.** A Waveshare 2.13inch e-Paper HAT V4 on the
  ESP's free edge. The collector sends an `EPD:` line of ten short strings every
  180 ticks, and once 10 ticks after it starts. The left column is temperature
  in F and C (the BME280's), humidity, CO2, VOC index and NOx index. The right
  is energy, memory pressure, emitter, cell states and live links. The body only
  draws the text. The panel has a task of its own on the ESP's second core.

What was checked:

- The port against Sensirion's own C, compiled on the Mac, on 40,000 scripted
  samples. NOx matched on every sample. VOC differed by 1 on 84 samples and never
  by more. A save and restore mid-run changed nothing.
  `python tools/gas_index_check.py` repeats it against stored reference values.
- The Creature is unchanged by any of it. `tools/scripted_body.py`, the committed
  collector against the changed one, seeds 1, 2, 3 and 7: every command to the
  body, every table and every saved file identical, apart from the new `air_log`
  table, the new snapshot keys and the `EPD:` lines. The `--twin` gate passes
  6/6. The twin's own numbers differ between any two runs, with or without a
  change, so they are left out of that comparison.
- On the body: all five I2C addresses answer. A full e-paper refresh takes 3.6
  seconds and a partial one 0.5. While the panel drew, the largest gap between
  samples was 103 ms, the normal spacing.

What was found:

- **The body's WiFi signal is very weak.** A diagnostic sketch measured the home
  network at -93 to -95 dBm at the body and saw only one or two networks at all.
  One join took 15.7 seconds and the next failed after 25. The firmware used to
  restart the attempt every 10 seconds, which cut slow joins off; it now waits
  30. With that it joined at -89 dBm, then dropped three times in five minutes,
  one of them a connection reset by the body. After that it held. The cause is
  not established. The two candidates are the antenna (it lies flat over the
  breadboard, with the amp and new jumpers close by) and power (the 3V3 rail now
  feeds more parts). The body had joined in 4 seconds earlier the same day.
- After a flash the body often does not join WiFi until it is reset, sometimes
  twice.
- The CO2 sensor's temperature and humidity read about 3 C warm and 8 % dry for
  the first minutes after a boot, then settle near the BME280's.
- The dashboard service needs a restart when `server.py` changes. `sudo` on the
  Pi asks for a password; the service runs as `josh` with `Restart=always`, so
  stopping its process brings it back in about five seconds.
- A mistake worth remembering: a collector change crashed at its first tick
  under the scripted body, and the comparison reused the previous run's output
  files and looked clean. Delete old outputs and check the exit code.

Open from this build:

1. The weak WiFi. The status line now carries `wifi_rssi`, but only on the wire:
   the collector does not log it. Watch the serial output with the UART cable in,
   move the antenna end clear of the breadboard, and see what the number does.
   Check the 3V3 rail with a meter while the panel refreshes and WiFi transmits.
2. The air senses are not part of the Creature. Whether they should be, and
   where they would enter the ring, is a design question, and any answer needs
   its own gate.
3. The loops have not been measured on the real body since the air sensing and
   the panel task were added. This joins item 1 of the older list: rerun the
   loop probe.
4. The words on the index tiles (VOC raised at 150, high at 250; NOx at 20 and
   150) and the 12 hour limit on a saved baseline are first guesses.
5. `air_log` has no retention. It grows by 4,320 rows a day.
6. Whether every e-paper line fits its column has not been confirmed line by
   line. The panel redraws every three minutes, Waveshare's recommended minimum.
7. The NOx index needs about five hours from a fresh baseline before it means
   much.

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
   although the Sensors panel is hidden there. Since v07.0 that includes the air
   readings and the gas index. Decide whether that is wanted.

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
| gas index port | `python tools/gas_index_check.py` | PASS |

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
| `CREATURE_PAPER` | 1 | the `EPD:` text for the body's e-paper |

In the field, `EVENT_MODEL = "pressure"` and `LOOP_FEEL_GAIN = 0` are the controls
for the event rule and the felt loop.

Deploying to the Pi. Push `main`, then on the Pi fast-forward its `v06` branch to
`origin/main` (a plain pull says it is up to date), stop the collector in its tmux
session, start it again. Before each restart the saved state was copied next to
the database with a name such as `.pre-twospeed.json` on the end.

The start command in force since 7 October, typed in the tmux pane (in
`~/Creature/Code/Python`, venv active). Leave any part out and that part is off:

    CREATURE_EXPRESSION_MODEL=relative CREATURE_SLOW_MIX=0.25 \
    CREATURE_SOFT_CEILING=1 CREATURE_CEILING_KNEE=1.0 CREATURE_SLOW_LEAK=4.13e-7 \
    CREATURE_BATTERY_CEILING=1 CREATURE_COLOUR_MODEL=inner \
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
carries the page to the public mirror within a minute. A change to `server.py`
needs the dashboard service restarted as well; see the v07.0 section for how.

Flashing the body. Plug the ESP's UART socket into the Pi. It shows up as
`/dev/ttyACM0`, not `ttyUSB0`. Build and upload with the PlatformIO in the Pi's
firmware folder. Opening that serial port resets the ESP. After a flash, watch
the serial output for `wifi_ready` before starting the collector; if it only
prints `wifi_reconnecting`, reset the body. A bench sketch can be built from a
scratch folder on the Pi outside the checkout, with the same PlatformIO, so the
checkout stays clean. Flashing a bench sketch takes the Creature offline until
the main firmware is flashed back.
