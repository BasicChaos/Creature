# Creature — project context

Creature is Josh Gnizak's experiment in building a small artificial organism, not an AI assistant. An ESP32-S3 body senses a room (light, sound, motion, weather) and renders light and sound. Since build v07.0 (4 October 2026) it also carries two air sensors (CO2, VOC and NOx) and an e-paper readout, which the field does not use yet. A MAX17048 fuel gauge reports the battery (since 6 October 2026, also outside the field for now). A Raspberry Pi mind runs a twelve-cell field, a forward model of its own outputs, and the collector loop that ties them together. A public dashboard mirrors it at basicchaos.com/creature/.

This file is background for a new chat. Read these two first:

- `Creature v07.0 Status and Next Steps.md`: where things stand as of the evening of 4 October 2026 (loop findings on the body from 2 October), what is switched on at the Pi, what was found, and the open items in order. Start here.
- `Creature Project Design Document Active.md`: the canonical design. It is kept in step with the code.

## Ground principles (Josh's)

- It should be the machine that it is. It will never come alive, and it will always be influenced by him. No persona, no chat, no pretending.
- The long-term direction is graph and vector memory, with a language model as the dream state that prunes and strengthens. The model only proposes edits; it never drives the body.
- Keep the hardware as it is. Upgrade only when needed.
- The body stays dumb, the mind stays changeable. The ESP reads sensors and renders commands. Everything that learns lives on the Pi.

## Rules for working here

- Whenever you change a file, give Josh a clear list of changed files at the end of the reply.
- Never edit the Obsidian vault (the subfolder also called `Creature/`) without explicit permission. It is gitignored.
- No tuning or behavior change ships without a control-versus-variant run in `Code/Python/tools/field_lab_v06.py`. Add a gate for each new build step, with a fixed seed. Check it on seeds 1, 2, 3 and 7.
- Plain Python in the mind and the tools. No numpy, so runs reproduce exactly.
- Logic lives once, in `Code/Python/mind/`. The collector and `field_lab` both import it.
- After any change to the dashboard, start the local preview (`creature-dashboard-test` in `.claude/launch.json`, port 8791), leave it running, and give Josh the link.
- Commit only when asked, straight to `main`, with plain messages. No co-author or attribution lines. Push only when asked.
- Josh understands code but is not a strong coder. Say what file, what changed, and why, in plain words.
- Report honestly. When a gate fails, say so and say why. Several of this project's real findings came from a first design failing its own gate.

## Layout

```
Code/
  Firmware/esp-creature-core/   the ESP32-S3 body (PlatformIO)
  Python/
    collector/collector.py      the runtime loop on the Pi
    mind/                       cell_field_v06, forward_model_v06, curiosity_v06,
                                expression_v06, expression_memory_v06, twin_v06, normalize,
                                gas_index (dashboard only; the field does not read it)
    tools/                      field_lab_v06 (gates), loop_probe (real body),
                                fake_body (live stand-in for the ESP),
                                scripted_body (the collector on a scripted body, repeatable)
    dashboard/                  server, page, static export, VPS sync
    data/                       local data, gitignored; data/replay/ has recorded senses
Hardware/                       KiCad, wiring, bench notes
```

`instructions.md`, `AGENTS.md` and `tmux_creature.md` are local notes, gitignored. `instructions.md` has the connection details and the flashing procedure.

## Running things

From `Code/Python`:

- A gate: `python tools/field_lab_v06.py --voice` (also `--forward`, `--events`, `--feel`, `--curious`, `--history`, `--twin`, `--battery`, `--colour`, `--palette`, and the older ones). `--history` measures the memory horizon; with `--json` then `--compare` it judges a variant against a control. Use `--history-hours 72` on recorded senses.
- A replay on recorded senses: add `--replay data/replay/<csv> --state data/replay/<json>` to `--events` or `--voice`.
- The collector against no hardware: start `tools/fake_body.py`, then run the collector with `CREATURE_DB_PATH` and `CREATURE_STATE_JSON_PATH` pointed at a scratch folder. Its docstring has the commands.

## The Pi

- `ssh creature` works from this Mac on the home network.
- The Pi's checkout is on a branch called `v06`. A plain pull reports nothing new. Update it with `git fetch origin && git merge --ff-only origin/main`.
- The collector runs by hand in a tmux session named `collector`. The dashboard is a systemd service and needs no restart for a change to `index.html`. A change to `server.py` does need one; `sudo` asks for a password, so stop its process and systemd brings it back (the status doc says how).
- The collector is started with settings in front of the command: the relative expression model and two-speed links (since 4 October 2026), and, since 7 October 2026, the battery setting the energy reserve's ceiling, the inner colour model and the open voice palette. A plain restart silently turns them off. The full start command is in the status doc under "Deploying to the Pi". Before any restart, read what is in force from `/proc/<pid>/environ` and start it the same way unless Josh says otherwise.
- The body runs on a battery when its power cable is out. If the collector prints `Reconnect failed` and the body does not answer a ping, it is the body, not the Pi.
- The body accepts one TCP connection, so stop the collector before running `tools/loop_probe.py`.
- Flashing is done from the Pi, with the ESP's UART USB socket plugged into it (`/dev/ttyACM0`). After a flash the body often needs a reset before it joins WiFi. Its signal is weak; the status doc has the measurements.
- The file names keep `v06` (`cell_field_v06.py`, `field_lab_v06.py`). Only the version label moved to v07.0.
- Reading the live state is safe: `/dev/shm/creature/creature_state.json`. The database is large; query it by id range, not by scanning.

## Related project

Josh publishes updates about Creature on basicchaos.com, from `~/Documents/PROJECTS/basicchaos` (its own `CLAUDE.md` and `AGENTS.md` apply there). Never push that repo unless he asks: a push to its `main` publishes the site.
