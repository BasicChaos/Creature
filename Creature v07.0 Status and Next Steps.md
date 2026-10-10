# Creature v07.0: status and next steps

Written 2 October 2026, updated 3 October for the dashboard work, 4 October for
the memory-horizon work, and the evening of 4 October for build v07.0. It is the
handoff for whoever picks this up next. The design itself is in
`Creature Project Design Document Active.md`. This note is the state of things,
what was found, and what is still open. The findings on the real body further down
are still those of 2 October. The newest section is the one for 8 and 9 October,
just below the list of where things are.

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

## The light loop is closed: the loop probe, rerun (9 October, 19:16)

The light half of `tools/loop_probe.py` was run on the real body (sound skipped).
The collector was down from 19:14:32 for about two and a half minutes and was
started again with the same eight settings (state loaded from tick 1345515). No
code changed. The report is next to the database as
`loop_probe_20261009_191631.json`.

| Frame (channel value) | Lux added, 9 October | Lux added, 3 October |
|---|---|---|
| white (200) | 240.0 | 0.9 |
| red (200) | 62.6 | 0.33 |
| green (200) | 137.8 | 0.55 |
| blue (200) | 36.5 | 0.08 |
| all four (200) | 410.9 | 1.54 |
| all four (100) | 214.8 | 0.81 |

- The room was at 149 lux with the strip dark, and the sensor's noise was 0.40
  lux. The strongest frame is 822 times the noise. On 3 October it was 22 times.
- The sensor sees the strip about 270 times more strongly than on 3 October.
  Josh: the strip sits at different distances from the sensor at different
  times, and on 9 October it was about 1 to 2 inches away. So the coupling is not
  a fixed number. Nothing may assume a distance; the light model has to keep
  learning it, and these lux figures hold only for this placement.
- Josh does not want the strip flashy in a dark room. As things stand the
  curiosity probe works against that: in a quiet room it lifts the white channel
  by up to 0.35 of the cap in bursts, which at this distance is about 80 lux at
  the sensor, and the inner colour model's 0.6 does not dim it.
- It is close to linear: half the value gives 0.52 of the lux.
- Per unit of channel value: white 1.2 lux, green 0.69, red 0.31, blue 0.18. Green
  counts nearly four times blue. So the inner colour model's turning hue already
  reaches the sensor as a change in brightness, at the same strip brightness.
- At full value the strip adds more light than the whole room (411 against 149
  lux). The room's light sense is therefore partly a reading of the strip itself.
  The swing of about 20 lux from tick to tick, noted on 2 October, is the size an
  ordinary frame would give.

This settles older open item 1 as far as the measurement goes, and makes item 7
due (the design doc and the dashboard legend still say the light loop is open).
Item 2, one weight per pixel for the light model, is now the main thing. Not yet
done: the same probe in a dark room.

## The body sat on a dead WiFi link until reset (10 October, 09:07 to 09:29)

The body went silent at 09:07:44 and stayed away for 22 minutes, until Josh
pressed reset. No ping from the Mac or the Pi in that time. The Pi and the
collector were fine and the collector reconnected by itself at 09:29:43.

What Josh saw before the reset:

- The repeater still listed the body (`esp32s3-85A690`, 192.168.178.91) as
  connected on 2.4 GHz, at the weakest signal step and 5 down / 14 up Mbit/s.
  Earlier that morning it had 26 / 70.
- The board's small light was blinking fast. That light follows the USB serial
  line: the firmware sends its 10 Hz samples there only while no collector is
  connected over WiFi. So the firmware was running normally and knew it had no
  collector.

Reading (not proven; the serial output was not watched): the body and the
repeater both believed they were connected, and nothing was getting through. The
firmware only rejoins WiFi when it sees the link as down (`WiFi.status() !=
WL_CONNECTED`), so it never tried. A reset makes it join afresh, which is why
that worked.

It is not the dark-room rule or anything on the Pi: the body could not be
pinged from the Mac either. Breaks like this are not new (the tick log has some
on every day of the week before), but 10 October had more, after changes to the
repeater's and the router's settings at 08:14 and 08:35 and the repeater
narrowing its 2.4 GHz channel at 08:23.

The Pi's move to the 2.4 GHz band was at 08:13:21, ten minutes before the
body's first break of the morning (08:23:32) and nine and a half before the
repeater narrowed its channel. It cannot have started the trouble: the breaks
at 23:53 and 23:57 the evening before, and those of the week before, came
first. Whether it made the morning worse is open. Since the move, the Pi, the
repeater's link to the router and the body all share the same 2.4 GHz channel.

Josh: this was not a problem before. The tick log agrees, and puts the change
at the v07.0 rebuild of 4 October, not at anything done on 9 or 10 October.
Breaks of 20 seconds to an hour, per day (planned restarts and flashes are in
these counts too):

| Days | Breaks a day | Minutes lost a day |
|---|---|---|
| 20 September to 3 October (12 days of running) | 0 to 2, once 5 | 0 to 14, once 63 |
| 4 to 9 October | 2 to 12 | 6 to 92 |
| 10 October, first 2.2 hours | 3 | 28 |

That is the day the air sensors and the e-paper went on, and the day the
body's WiFi was first measured as very weak (-89 to -95 dBm, see "Build v07.0"
below: the antenna lying flat over the breadboard, or the 3V3 rail feeding more
parts). It was never followed up. On 10 October the repeater showed the body at
its weakest signal step. So the weak link since the rebuild is the root, and
the router's re-tuning and the dead link are what a weak link does. The log
only covers June and from 20 September; June, on the earlier body, had breaks
of its own.

Next for this, in order: log the body's `wifi_rssi`, then move the antenna
clear of the breadboard and the panel and watch the number, then check the 3V3
rail.

The first is done (10 October). The collector now reads `wifi_rssi` from the
body's status line, which comes every two seconds. It is in the live snapshot
as `sensors.wifi_rssi` (dBm; empty when the last reading is over 10 seconds
old) and in a new `wifi_rssi` column of `power_log`, a row every 20 ticks.
Around -60 is good; under -85 is too weak to hold. The dashboard does not show
it yet. Nothing else changed: on the scripted body, seeds 1, 2, 3 and 7, a run
is identical to the code before, and `tools/scripted_body.py --wifi-rssi -70`
makes the scripted body send the status line.

A fix on the body, not built: if the link reads as up but nothing has come
from the collector for a minute or two, rejoin WiFi; if that keeps failing,
restart. It needs a flash. With the Pi down for hours, as on the night before,
it would rejoin WiFi every couple of minutes, which is harmless.

## The Pi lost its WiFi overnight (10 October, 00:53 to 07:52)

The collector's last tick was logged at 00:53:18 on 10 October (tick 1364851),
and the public mirror's last sync was at 00:53:09. From then the Pi answered
neither ping nor ssh from the Mac, while the body still answered pings. Josh's
dashboard page said "server unreachable" (that is the page's own message when
it cannot fetch from the Pi). He rebooted the Pi at about 07:47. The collector
was started again at 07:52 in a new tmux session with the same nine settings
(state loaded from tick 1364800, saved at 00:52; about 50 ticks and seven hours
were lost).

What is known:

- The Pi did not crash or lose power. A systemd timer (`apt-daily-upgrade`) ran
  on it at 06:41, in the middle of the silence. It was running with no network.
- It did not get its WiFi back in seven hours, though the connection is set to
  reconnect by itself. That looks more like the WiFi chip or its driver hanging
  than like a signal that came and went.
- The Pi's WiFi link is weak: -77 dBm on the 5 GHz band (channel 44), sending
  at 12 Mbit/s, measured after the reboot. It is a Pi 3 B+ on WiFi only; the
  ethernet socket is unused.
- There were shorter breaks in the ticks before it: 2 minutes at 18:47 on
  9 October, 4 minutes at 23:53 and 10 minutes at 23:57. Whether those were the
  Pi's link or the body's is not known. The one at 18:47 was before the
  dark-room rule was switched on (20:07).
- The Pi reported under-voltage 18 seconds into the new boot
  (`throttled=0x50000`, `Undervoltage detected!` in the kernel log). Its power
  supply is marginal, at least while booting, with the SSD on its USB.
- The body's battery was fine (4.11 V, 90.7 %) up to the last reading.

What is not known: why the WiFi dropped at 00:53 and why it stayed down. The Pi
keeps its system log in memory only (`Storage=volatile`, the Raspberry Pi OS
default), so that night's log went with the reboot. The router's event log may
show something at 00:53.

The router's event log (FRITZ!Box 7590) has nothing between 21:03 on 9 October
and 07:58 on 10 October, so the router did not drop the Pi; the Pi went quiet
on its own side. A scan from the Pi on 10 October: the 2.4 GHz band reads 65 of
100 (channel 5, with a second access point at 59), the 5 GHz band it was using
reads 42. The body is 192.168.178.91 and the Pi 192.168.178.87.

Nothing points at the dark-room rule: it does not touch the network, and the
Pi itself kept running.

To be ready for the next time (each needs Josh, `sudo` asks for a password):

1. Keep the system log on disk, so the evidence survives a reboot. **Done by
   Josh on 10 October** (`/etc/systemd/journald.conf.d/50-persistent.conf`,
   `Storage=persistent`, at most 200 MB). After a silence, read the hours
   before it with `journalctl -b -1`.
2. Move the Pi onto an ethernet cable, or onto the 2.4 GHz band, which reaches
   further. **Done by Josh on 10 October at 08:13:21** (the Pi's log has it;
   it was back on the network six seconds later): the Pi's connection is
   set to the 2.4 GHz band (`802-11-wireless.band bg`). It reads -62 dBm and
   72 Mbit/s there, against -77 dBm and 12 Mbit/s on 5 GHz. The collector lost
   the body for a few seconds at the switch and reconnected by itself. To undo:
   `sudo nmcli connection modify 'netplan-wlan0-FRITZ!Box 7590 ST2'
   802-11-wireless.band ''`, then bring the connection up again.
3. A small watchdog that restarts the network when the router stops answering.
   It must not reboot the Pi: the collector is started by hand and would not
   come back.
4. A stronger power supply for the Pi.

The last snapshot before the silence, the first seen from a dark room: calm
1.0, the room read at 0.0 lux, the strip credited with 12.1 lux.

What the strip did in that first dark evening, from `loop_log` (the sum of the
four channels' mean values in each frame sent, in ten-minute steps). The room
went dark at about 20:10, three minutes after the rule was switched on, and
stayed dark to the silence at 00:53. Beside it, the dark part of the night
before (23:10 to 00:50 on 8 to 9 October), which had no rule:

| In the dark | 8 to 9 October, no rule | 9 to 10 October, rule |
|---|---|---|
| Strip value, usual | 46 to 70 | 15 to 27 |
| Strip value, highest | 68 to 174 | 30 to 56 |
| Change from tick to tick, usual | 1.1 to 2.2 | 0.25 to 0.6 |
| Change from tick to tick, biggest | 10 to 47 | 2.0 to 3.5 |
| White channel, highest mean | up to 22 | 0 (3 once, near the end) |
| Raw lux at the sensor | 13 to 24 | 2.7 to 8.9 |

In the lit evening before the dark (19:20 to 20:00 on 9 October) the usual
value was 73 to 85 and the biggest change 27 to 107, the same as the night
before. So on the real body the rule did what the gate said. Two limits: the
log holds each frame's channel means, not its pixels, so it cannot show single
pixels switching on and off; and the two nights were not the same room at the
same hours.

What the field did in that dark evening (20:10 to 00:53), from the log tables:

- It did not go quiet. Events on 11 to 17 % of ticks all evening, against 14 to
  16 % in the lit hour before. The room was not still: the sound sense was above
  zero on 14 to 31 % of ticks until midnight, and on 6 to 10 % after.
- Of 159 events, 105 were led by the sound cell (2) and none by the light cell
  (8) or the LED-and-light loop cell (7). In the lit hour before, the light
  cell led 42 of 84. In the dark of the night before, with no rule, the loop
  cell led 10 of 91.
- The light sense read exactly zero from 20:30 on, and felt light was zero on
  every tick. With no rule the night before, felt light was above zero on 16 to
  69 % of dark ticks (small: 0.017 at most on average). So in the dark the
  light loop is silent under the rule. That is the price the gate printed.
- One sleep, at 00:42, 29 ticks, for low stimulation, 4 links pruned. The night
  before had one like it at 22:55.
- The links held: mean weight 1.173 at the start and 1.181 at the end (it
  dipped to 1.094 on the way). The night before they fell from 1.120 to 1.063.
- Energy stayed at 5.8, tones ran at about 25 an hour until midnight and 17
  after. Curiosity proposed 13 light probes after 00:30 as things got quieter;
  none showed on the strip.

So the gate's price (a still dark room becomes a silent hour) was not tested:
there was sound in the room all evening. A still, dark, empty room under the
rule has not been seen yet. The two nights also differ in energy (about 2 on
8 October, 5.8 on 9 October) and the speaker made no tones on the 8th.

The body went off the network at 08:23:29 on 10 October (no ping from the Mac
or the Pi; the collector prints `Reconnect failed`). Its battery read 4.12 V and
91.5 % on the last sample. It came back by itself: ticks stopped from 08:23:32
to 08:27:54 (262 seconds) and again from 08:28:26 to 08:29:47 (81 seconds), and
the collector reconnected each time with nothing restarted. The first break is
the same length as the one at 23:53 the evening before (261 seconds), which
suggests that one was the body too.

The router's log explains this morning's: the repeater, which the body is
joined to, narrowed its 2.4 GHz channel at 08:22:49 and again at 08:23:24
("Wi-Fi transmission quality increased by reduced channel bandwidth"), five
seconds before the body went. Josh had changed the repeater's settings at
08:14. So the repeater dropped the body, not the other way round. What is the
body's own is how long it takes to get back: about four and a half minutes,
both times. Its firmware gives up a join after 30 seconds and starts again
(`WIFI_RETRY_INTERVAL_MS`, `WiFi.disconnect()` then `WiFi.begin()`); with its
weak signal a join may need longer than that, so it may be cutting its own
attempts short. That is a guess until the serial output is watched during a
drop. The router's log shows nothing at 23:53 the evening before.

## A calm strip in a dark room: switched on at the Pi (9 October, 20:07)

Josh does not want the strip flashy in a dark room. `mind/calm_v06.py` (new) is
a rule for that: the darker the room, the dimmer and slower the strip. The
collector applies it when started with `CREATURE_DARK_CALM=1`. It is off by
default.

**It is on at the Pi.** The Pi runs commit `6098dfd`. The collector was
restarted at 20:07 on 9 October with nine settings, the eight from before plus
`CREATURE_DARK_CALM=1` (state loaded from tick 1348495, under a minute down).
State from before the restart is next to the database as `*.pre-calm.json`. To
go back, restart without the setting.

The first minutes on the real body, in a lit room: the rule read the room at
127 to 136 lux and credited the strip with 8 to 17 lux, which adds up to the
raw reading (136 to 162). Calm stayed at 0, so the strip was untouched. **It
has not yet been seen in a dark room.** Read it with the `calm` block of
`/dev/shm/creature/creature_state.json`.

What the rule does:

- It works out the room's own light level. Dark is 3 lux or under, lit is 40 or
  over, judged on a log scale in between.
- In a dark room each frame is shown at 0.35 of its brightness, and the strip
  moves only 0.12 of the way to each new frame per tick. That takes the probe's
  white bursts, the event flash, the shimmer and the pulse down with it.
- In a lit room the frame goes out untouched, the very same numbers.
- Going dark takes hold within about 7 ticks. Coming back takes a little over a
  minute, then eases in over 20 ticks.

How it knows the room is dark with the strip beside the sensor. It learns, from
how the lux reading moves as the frame changes from tick to tick, how much the
sensor sees of the strip (the whole of it, and each quarter), and takes that
light out. It assumes no distance. It counts the strip's light in the steps the
body really shows (the firmware's cap of 40 means a value under 7 shows
nothing). Nothing of it is saved; after a restart it learns again.

`tools/field_lab_v06.py --dark` is the gate: a lit hour, a dark still hour, a
dark busy hour, the light back on; rule against no rule; the strip at three
distances (as on 3 October, as on 9 October, and twice as near again). 15/15 on
seeds 1, 2, 3 and 7. At the distance of 9 October, seed 1, in the body's
brightness steps summed over the strip:

| | No rule | Rule |
|---|---|---|
| Dark and still: strip light | 167.3 | 3.7 |
| Dark and still: biggest change of the strip in one tick | 100 | 16 |
| Dark and busy: strip light | 181.6 | 62.5 |
| Dark and busy: biggest change of the strip in one tick | 148 | 15 |
| Dark and busy: usual change from tick to tick | 9.05 | 1.77 |

The collector on the scripted body (`tools/scripted_body.py`, which gained
`--dark-after N`: the room's light goes out after N seconds), with the Pi's
eight settings, seeds 1, 2, 3 and 7:

- Rule off: everything the Creature did is identical to the committed code
  (commands, every log table, saved files, last snapshot). Only the twin
  differs, and it differs between two runs of the committed code as well.
- Rule on, 15 lit minutes then 30 dark: the lit quarter hour is identical to
  rule off. In the dark the strip's light is 50 against 189, its biggest change
  in one tick 10 to 15 against 171 to 177, its usual change 2.0 against 12.1.
- With the rule on the live snapshot gains a `calm` block (`calm`, `room_lux`,
  `own_lux`). The dashboard does not show it.

It took five designs, and the failures are worth keeping:

1. The room's level from the lowest reading of the last minute alone. 7 of 14.
   With the strip close, its own light made a dark room read as lit.
2. One weight per pixel, each corrected a little every tick. Better, but it let
   go of the dark for half a minute when the strip woke, because it counted the
   values sent and not the steps the body shows.
3. A creature started in a dark room stayed calm, so its strip barely moved, so
   it learned too slowly. Learning from smaller frame changes fixed that.
4. That passed the lab gate, with a safety margin that only worked in a narrow
   band. Then the collector on the scripted body showed it was wrong: in a room
   whose light drifts up and down, correcting every tick chases the drift. It
   credited the strip with 41 to 58 lux against a true 9 and went fully calm in
   a 54 lux room. The lab gate's rooms did not drift like that.
5. Now it gathers evidence over many ticks and solves for the best fit, with
   the slow drift taken out first. On the scripted body its guess of the
   strip's light is off by 1.1 lux on 7. The safety margin (the strip is
   credited 0.15 over what was learned) is no longer delicate: 0.15 and 0.3
   both pass; with none, a creature started in the dark at the nearest distance
   lets go now and then.

The price, which the gate prints and does not judge. In a still dark room with
the strip close, the Creature without the rule keeps stirring itself with its
own light: events on 10 to 16 % of ticks, mean arousal about 0.33. With the
rule that hour is quiet: no events, arousal 0.07, a little more sleep, and the
links end lower (0.61 to 0.68 against 0.78 to 0.92). That is the same quiet a
dark room had for the whole of the Creature's life before the strip sat close;
against a far strip with no rule the links end slightly higher (0.56 to 0.59).
The light loop stays closed in a lit room.

Two things I changed in the gate after seeing numbers, so they are not hidden:
one pixel may change by up to 4 of the body's steps in a tick (first set at 2;
it reaches 3 in the busy dark against 35 without the rule), and "not harmed"
uses the `--curious` gate's bar of full arousal on under a tenth of ticks (it
reaches 5.4 % on seed 3).

The collector prints a `Dark-room calm:` line at start.

Not done, in order:

1. The real strip has not been watched under the rule in the dark. There most of its
   values fall to 0 to 2 of the body's steps, so single pixels may be seen
   switching on and off. The real sensor's readings also come in steps of 0.83
   lux and with the BH1750's lag; the scripted body has both, the lab gate
   neither.
2. The lab gate has no room with drifting light. It should get one, since that
   is what caught design 4.
3. The rule learns its own picture of what the sensor sees. The forward model's
   light part needs the same thing (older open item 2). They should become one.
4. The brightness swing as expression (Josh's question that started this) would
   sit inside this rule: full depth in a lit room, nearly still in a dark one.
5. The dashboard could show the room's level and how calm the strip is.

## The speaker's own noise, the e-paper, and a collector crash (8 and 9 October)

Where it stands at 09:16 on 9 October: the Pi runs commit `3234e34`. The
collector was restarted at 09:16 with the same eight settings as before (state
loaded from tick 1316254, six minutes down). The body runs the firmware flashed
at about 15:20 on 8 October. The speaker is unmuted. The flashing cable is out
of the Pi. State from before the restart is next to the database as
`*.pre-probefix.json`.

**The amp makes loud noise by itself. Open, and the first thing to fix.** Twice
on 8 October the speaker sounded without the Pi or the firmware asking for it.

- 02:42:28: the body's microphone went from quiet to near the top of its range
  within one second, stayed there for 2 seconds, then about 48 seconds of ragged
  loud sound, between half and three times the level of a normal tone. It
  stopped by itself at 02:43:18. Josh, woken by it, heard random loud noises,
  quick and messy. He muted the speaker at 02:43:05 and pulled the power and the
  flashing cable by 02:44:12.
- 16:54:40: one loud second. Josh heard an alarm-like sound, loud and rising in
  pitch, and pulled the power at once. The speaker had been muted since
  15:44:06.
- Neither was a tone. The Pi logs every `VOX:` it sends: the last one before the
  first event was at 00:07:57, and before the second at 15:43:52. While the
  firmware plays a tone it stops sampling, so a tone's second holds 4 to 6
  readings; every second of both events holds the full 10. The firmware can
  only play a fixed pitch, not a rising one. The body did not restart either
  time. So the mind, the open palette and the tone code are ruled out.
- What the two share: the charger was in, and the flashing cable from the Pi to
  the ESP's UART socket was in (from 20:55 on 7 October to 02:44, and from 15:14
  to 18:19 on 8 October). The first night was the first one with that cable
  left in. The Pi's kernel log has no USB event at either moment and nothing on
  the Pi had the serial port open, so it was not a command over serial. The
  cable does give the body a second 5 V feed and a ground path through the Pi's
  hub. During the first event the cell's voltage dipped 50 mV. At the first
  event the charger had levelled off at 4.18 V, 92 %, a few minutes before.
  Whether the cable is the trigger is not shown: two events are not proof.
- Why the amp can do this: its shutdown pin (SD) is left floating, so it is
  always on, and it runs straight from the 5 V rail on a breadboard
  (`Hardware/Creature v06/WIRING v06.md`). Anything on that rail or on its
  three signal wires comes out of the speaker at full gain.
- Next: keep the flashing cable out except while flashing, and see whether the
  noise returns. The fix either way is to wire SD to a free GPIO with a
  resistor to ground, and have the firmware switch the amp on only while a tone
  plays. One wire, one resistor, a small firmware change. Not done.
- Also unexplained: the sound that woke Josh came with no warning in the record,
  and a hit of the same size at 16:48:03 may have been the amp or a hand on the
  board.

**The open palette's first evening.** Between 22:00 and midnight on 7 October it
sent about 20 tones an hour, up to 750 Hz, against about 10 an hour on the
nights before. After 00:07:57 it sent none until the event at 02:42, with the
speaker unmuted. Why it went silent is not looked into.

**The e-paper went blotchy.** Text that had not changed faded to grey while
digits that had just changed were full black. That is the partial refresh: only
changed pixels get a proper push, and the rest drift toward grey with each pass.
`PAPER_FULL_EVERY` in the firmware went from 10 to 3, so a full refresh comes
every 9 minutes, not every 30 (commit `3234e34`). Flashed at about 15:20 on
8 October, built from a scratch copy on the Pi. The body did not rejoin WiFi
after the flash, as before; a reset over the serial line and a minute's wait
brought it back at 15:23. The collector was left running and reconnected by
itself. Not yet judged on the panel. The photo was taken at a charge of 5 %,
and a low cell may add to the grey; that is not separated out.

**The collector crashed on its first probe tone under the open palette.** At
09:09:59 on 9 October: `TypeError: 'NoneType' object is not callable` in `send`.
The palette commit (`92d3ecc`) made the probe tone call the function
`voice_command`, while a few lines above the same name was a local variable,
empty in exactly that case. It took a day and a half to happen because a probe
tone needs the speaker unmuted, the field silent, and curiosity choosing a
voice probe. The local is now `field_command` (commit `6b1ce10`). Checked on the
Mac with a stand-in transport: the probe tone goes out as `VOX:450.0,300,0.40`.
No probe tone had come up on the Pi when this was written. The collector saved
its state as it fell, so nothing was lost but the six minutes. Neither
`field_lab` nor `scripted_body` caught it; a scripted run with the speaker
unmuted and a forced voice probe would have.

**The battery ran down again overnight.** With the power out since 16:55 on
8 October the cell reached 3.31 V at 01:58 on 9 October, and the body sent
nothing until 07:58, when it was charging again. That is where the low-battery
reflex puts the body to sleep (3.30 V), but whether it slept or simply lost
WiFi was not checked.

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

The body was flashed with this at 20:58 on 7 October (commit `b7c5dc4`) and the
collector restarted at 21:02 with its seven settings. The sweep on the real
speaker, volume 0.70, what the body heard at each pitch: 300 Hz 7,900; 378 Hz
28,900; 476 Hz 94,200; 600 Hz 100,900; 756 Hz 195,800; 952 Hz 333,100; 1200 Hz
241,600; 1512 Hz 229,400; 1905 Hz 172,300. Josh listened to the shapes and
overtones and chose: fewer beeps, more swells and soft tones, a mix of
overtones, and the top half only very lightly because it is piercing.

The open palette, built the same evening and deployed as commit `92d3ecc`: the
collector was restarted at 21:24 with `CREATURE_VOICE_PALETTE=open` added, eight
settings in all (state loaded from tick 1226624, under a minute down; no flash).
State from before the restart is next to the database as `*.pre-palette.json`.
No tone had been heard under it when this was written. What was built:

- `mind/expression_v06.py`: `CREATURE_VOICE_PALETTE=open` (default `beep`,
  unchanged). Five main notes from 450 to 750 Hz, three light high ones, swells
  and soft tones, overtones from the strip's hue, balance and tempo read against
  its own last 32 tones.
- `mind/forward_model_v06.py`: the pitch bands follow the palette, and the model
  is told a tone's `level`. The saved file gains `pitch_low` and
  `pitch_octaves`; bands learned over the other range start fresh.
- `mind/curiosity_v06.py`: under the open palette a probe is a soft tone and
  keeps under 800 Hz.
- The collector sends the longer `VOX:` line and prints a `Voice palette:` line.
- `tools/fake_body.py`, `tools/scripted_body.py` and the lab's simulated speaker
  follow the sweep above 440 Hz and scale what is heard by a tone's level.
- `tools/field_lab_v06.py --palette` is the gate: 9/9 on seeds 1, 2, 3 and 7.
  Its first run failed (three tones in four high, no swells), which is why
  balance and tempo are read against its own past tones. `--voice`, `--colour`,
  `--battery`, `--twin`, `--forward`, `--curious` and `--feel` pass.
- The collector on the scripted body, seeds 1, 2, 3 and 7: with the default
  palette, identical to the committed code apart from the two new keys in the
  forward model file. With `open`, an hour: the same number of tones, the
  largest link gap 0.013, the sound model explaining 0.84 against 0.73.
- A mistake caught by the twin gate: a start-up print in the collector used a
  name that does not exist there, and the collector crashed at launch.

Open from the palette: the light notes are at volume 0.40, below the 0.65 to
0.90 the bench notes call the amp's clean range, and have not been heard. The
Pi's sound model has learned 220 to 440 Hz and will start its bands again. The
shapes have only been heard in the audition, not as the Creature uses them.

Also open: the body's measure of its own tone listens at the fundamental across the
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

The start command in force since 9 October (20:07), typed in the tmux pane (in
`~/Creature/Code/Python`, venv active). Leave any part out and that part is off:

    CREATURE_EXPRESSION_MODEL=relative CREATURE_SLOW_MIX=0.25 \
    CREATURE_SOFT_CEILING=1 CREATURE_CEILING_KNEE=1.0 CREATURE_SLOW_LEAK=4.13e-7 \
    CREATURE_BATTERY_CEILING=1 CREATURE_COLOUR_MODEL=inner \
    CREATURE_VOICE_PALETTE=open CREATURE_DARK_CALM=1 \
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
