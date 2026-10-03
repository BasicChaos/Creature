# Creature — project context

Creature is Josh Gnizak's experiment in building a small artificial organism, not an AI assistant. An ESP32-S3 body senses a room (light, sound, motion, weather) and renders light and sound. A Raspberry Pi mind runs a twelve-cell field, a forward model of its own outputs, and the collector loop that ties them together. A public dashboard mirrors it at basicchaos.com/creature/.

This file is background for a new chat. Read these two first:

- `Creature v06.9 Status and Next Steps.md`: where things stand as of 2 October 2026, what was found on the real body, and the open items in order. Start here.
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
                                expression_v06, expression_memory_v06, normalize
    tools/                      field_lab_v06 (gates), loop_probe (real body),
                                fake_body (test stand-in for the ESP)
    dashboard/                  server, page, static export, VPS sync
    data/                       local data, gitignored; data/replay/ has recorded senses
Hardware/                       KiCad, wiring, bench notes
```

`instructions.md`, `AGENTS.md` and `tmux_creature.md` are local notes, gitignored. `instructions.md` has the connection details and the flashing procedure.

## Running things

From `Code/Python`:

- A gate: `python tools/field_lab_v06.py --voice` (also `--forward`, `--events`, `--feel`, `--curious`, and the older ones).
- A replay on recorded senses: add `--replay data/replay/<csv> --state data/replay/<json>` to `--events` or `--voice`.
- The collector against no hardware: start `tools/fake_body.py`, then run the collector with `CREATURE_DB_PATH` and `CREATURE_STATE_JSON_PATH` pointed at a scratch folder. Its docstring has the commands.

## The Pi

- `ssh creature` works from this Mac on the home network.
- The Pi's checkout is on a branch called `v06`. A plain pull reports nothing new. Update it with `git fetch origin && git merge --ff-only origin/main`.
- The collector runs by hand in a tmux session named `collector`. The dashboard is a systemd service and needs no restart.
- The body accepts one TCP connection, so stop the collector before running `tools/loop_probe.py`.
- Reading the live state is safe: `/dev/shm/creature/creature_state.json`. The database is large; query it by id range, not by scanning.

## Related project

Josh publishes updates about Creature on basicchaos.com, from `~/Documents/PROJECTS/basicchaos` (its own `CLAUDE.md` and `AGENTS.md` apply there). Never push that repo unless he asks: a push to its `main` publishes the site.
