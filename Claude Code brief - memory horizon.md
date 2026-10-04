# Claude Code brief: memory horizon

Written 3 October 2026. Three steps. Do one step per task, then stop and report.

## Rules for this repo

`CLAUDE.md` applies in full. The ones that matter most here:

- Plain Python, no numpy. Logic lives once, in `mind/`.
- Every gate on a fixed seed. Check seeds 1, 2, 3 and 7.
- Never edit the Obsidian vault (the subfolder called `Creature/`).
- Commit only when asked.
- At the end, list every file you changed and say in plain words what changed.

## Why

The Creature learns, but its memory only reaches back about one day.

Test run on 3 October: the saved 9-day-old field (`data/replay/field_state_2026-10-02.json`)
and a fresh field were fed the same recorded day (the first 86,400 rows of
`data/replay/senses_2026-09-24_to_2026-10-02.csv`).

- After 24 hours, every ring link weight of the newborn was within 0.1 of the elder's.
- The gap in balance (B) fell from 0.10 in hour 0 to 0.010 in hour 23. The gap from
  only changing the random seed was 0.002 to 0.005.
- Ring weights are railed: 6 links near W_MAX (2.0), 4 at PRUNE_FLOOR (0.02).

"Memory horizon" = how long a newborn needs, on the same input, to become
indistinguishable from the elder. Today: about one day. This is the progress
number. A change to the mind counts as progress only if it pushes the horizon out
without making the Creature learn slower.

## Step 1: the history gate (measure only, no behaviour change)

Add `history_probe(args)` to `Code/Python/tools/field_lab_v06.py` and a
`--history` flag in `main()`, next to the other gates.

Three lives, same input, run with the field and the decoder only (like `_voice_run`):

- elder: a field that has already lived.
- newborn: `cf.build_field()`, nothing loaded.
- noise: the elder again with seed + 1. This sets the noise floor.

Two input modes:

- Real data: `--replay <csv> --state <json>`. Elder loads the state. Test input is
  the first `--history-hours` hours of the CSV (default 24, one row per tick).
- Synthetic (no data needed, used for the seed checks): raise the elder on
  `scenario_inputs("bursts", ...)` for `--raise-hours` (default 48), then give
  elder, newborn and noise the same `scenario_inputs("day", ...)` test day.

Record per tick: A, B, T from the decoder. Record every 600 ticks: the twelve
ring weights, sorted by link key.

Per hour, report:

- expression gap: mean |A_elder - A_newborn| + mean |B_elder - B_newborn|
- noise gap: the same, elder against noise
- link gap: the largest |w_elder - w_newborn| over the twelve links, at the end of the hour
- newborn learning: total change of the newborn's ring weights in that hour
- railed links: how many elder links are within 0.1 of W_MAX or at PRUNE_FLOOR

Two horizons, in hours:

- expression horizon: the first hour from which the expression gap stays at or
  below 2 x max(noise gap, 0.005) until the end of the test
- link horizon: the first hour from which the link gap stays at or below 0.1

If a horizon is never reached, print it as "more than N hours". That is the good outcome.

Print a short table, one row per hour, then the two horizons. With `--json`, save
the hourly rows and the horizons so `--compare` can diff a control against a variant.

Checks (these test that the measurement works, not the Creature):

1. history shows at all: the expression gap in hour 0 is at least 5 x the noise gap
2. the noise floor is small: the mean noise gap is under 0.01
3. the newborn learns: its weights move by at least 0.2 in total over the first 3 hours
4. the horizons are reported

Acceptance for step 1:

- `python tools/field_lab_v06.py --history --seed 1` passes, and on seeds 2, 3, 7.
- On real data it reproduces the numbers above (horizon about 18 to 24 hours,
  link gap under 0.1 by the end):

      python tools/field_lab_v06.py --history \
          --replay data/replay/senses_2026-09-24_to_2026-10-02.csv \
          --state data/replay/field_state_2026-10-02.json

- No file in `mind/` or `collector/` changes in this step.
- Add the gate as a row in the gate table in `Creature Project Design Document Active.md`.

## Step 2: a newborn twin on the Pi (watch it live, no behaviour change)

In `Code/Python/collector/collector.py`, behind `CREATURE_TWIN` (default 1):

- Build a second field, the twin, with `build_field()`.
- Load it from its own file next to the field state (`..._twin_v06.json`). If that
  file does not exist, the twin is born fresh. Deleting the file restarts it.
- Each tick, step the twin with the same four senses as the real field, and
  `loop=None`. The twin never sends anything to the body.
- Read the twin through its own `ExpressionDecoderV06` to get its A, B, T.
- Save the twin when the real field is saved, and at exit.
- Add a `twin` block to the live snapshot: twin age in ticks, its ring weights,
  and the gaps against the real field, each smoothed over about an hour:
  arousal gap, balance gap, largest link gap.
- Every 60 ticks, write one row to a new SQLite table `twin_log`:
  tick, logged_at, twin_age, gap_a, gap_b, gap_t, gap_link.
- Dashboard: one line in the Learning panel only. Example:
  "Newborn twin, 1 d 4 h old. Gap: arousal 0.01, balance 0.02, links 0.05."
  No other dashboard changes.

Acceptance for step 2:

- Runs end to end with `tools/fake_body.py` (see its docstring).
- The real field's output is identical with `CREATURE_TWIN=0` and `=1` on the same
  fake-body run. The twin must not change the Creature.
- Pi 3 CPU: the collector's tick time stays under 1 second with the twin on.

## Step 3: the first experiment, two-speed links (behaviour change, off by default)

Goal: memory that builds up over a week instead of resetting each day.

In `Code/Python/mind/cell_field_v06.py`, for the twelve trainable ring links:

- Keep today's weight as the fast weight.
- Add a slow weight per link. Each tick it moves toward the fast weight by
  SLOW_RATE (start at 1 / 604800, a time constant of about one week).
- Decay pulls the fast weight toward the slow weight, not toward the floor. Long
  history pulls the fast weight back.
- The drive uses w = (1 - SLOW_MIX) * fast + SLOW_MIX * slow.
- Soft ceiling: scale Hebbian growth by (W_MAX - w) / W_MAX, so weights approach
  the ceiling instead of sitting on it.
- New knobs: SLOW_MIX (default 0.0 = today's behaviour, the control), SLOW_RATE,
  SOFT_CEILING (default False).
- Save and load the slow weights. An older snapshot loads with slow = fast.
  Bump the snapshot version.

Gate, control against variant, seeds 1, 2, 3, 7 and real data:

    python tools/field_lab_v06.py --history --seed 1 --json control_history.json
    python tools/field_lab_v06.py --history --seed 1 \
        --set SLOW_MIX=0.5 --set SOFT_CEILING=True --compare control_history.json

The variant passes only if all of these hold:

1. the expression horizon is at least 2 x the control's
2. the newborn still learns: its weight change in the first 3 hours is at least
   70% of the control's (this rules out "it just learns slower")
3. fewer railed links than the control
4. these gates still pass with the variant set: `--forward`, `--events`, `--feel`,
   `--curious`, `--voice`

If it fails, report which check failed and why. Do not tune until it passes.
Leave SLOW_MIX at 0.0 in the code. Josh decides when to switch it on.
