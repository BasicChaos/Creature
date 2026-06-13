Creature Project Design Document

Version: May 31 2026
---

Runtime note: all Creature services (collector, mind, dashboard) run on the Raspberry Pi 3 B+. The Mac is only used for editing and Git. The ESP32 is the body and runs no logic.

Purpose

Creature is an experiment in building a small artificial organism rather than a traditional AI assistant.

The goal is not to create a chatbot.

The goal is to create a persistent entity that:

* Senses the world
* Builds memories over time
* Develops internal state
* Expresses itself through physical outputs
* Preserves continuity as it grows

The long-term vision is a system that begins as a simple organism and becomes increasingly complex without replacing its original core.

Current development focuses on architecture rather than intelligence.

How It Works Right Now

Read this one section to understand the whole system.

The Creature has a body and a mind. The body is an ESP32 board with a light sensor and an LED. It does nothing clever. It reads the light level many times a second, sends each reading to the Pi, and waits for commands telling the LED how bright to be. All thinking happens on the Raspberry Pi 3.

On the Pi, one program (the collector) runs this loop:

1. Read a light value from the ESP.
2. Save it to the database.
3. Update short-term memory: rolling averages over the last 3, 10, and 60 seconds. Comparing the 3s average to the 60s average shows how much the light just changed. That change is called novelty.
4. Feed novelty and the current light level into the cell network.
5. Read the creature's internal state back out.
6. Pick a behavior from that state.
7. Send the matching brightness and pulse speed to the LED.

The cell network is the creature's nervous system. It is three small cells, each holding one number between 0 and 1:

* Arousal: how stimulated it is right now. Novelty pushes it up. It fades fast.
* Fatigue: builds slowly while arousal stays high, and pushes arousal back down. This is habituation. Constant stimulation stops being exciting.
* Tonic: a smoothed sense of the ambient light level. A rough day/night signal.

The cells affect each other. Novelty raises arousal. Sustained arousal raises fatigue. Fatigue lowers arousal. So the creature reacts to change, then calms itself even if the change continues.

A separate behavior engine looks at that state and picks one behavior, by priority:

* Sleep: if fatigue is high. Dim, slow pulse. Overrides everything else.
* Startle: if arousal is high. Bright, fast pulse.
* Nominal: otherwise. Calm pulsing, brightness following the room light.

The key design choice: the cells only describe how the creature feels. The behavior engine decides what it does. Keeping these two apart means new feelings and new actions can be added later without tangling them together.

Everything the creature feels and does is written out twice each second: a live snapshot file the dashboard reads, and a history row in the database. The dashboard is a small web page served from the Pi. Open it locally from any device at http://<pi-ip>:8080 or remotely at https://basicchaos.com/creature/. The dashboard shows current light, internal state, behavior, charts over time, and a photo of the current Creature setup.

In one line: light comes in, becomes memory, memory becomes feeling, feeling becomes behavior, behavior drives the LED.

Current Hardware

Development Machine

MacBook Air M1

Purpose:

* Code editing
* Git management
* Documentation
* PlatformIO development

Runtime Machine

Raspberry Pi 3 B+ (hostname: creaturePi)

This is the live machine. The Creature runs here, not on the Mac.

Purpose:

* Runs Python services (collector, mind, dashboard)
* Stores data on attached SSD
* Builds memory windows
* Runs the cell network (arousal, fatigue, tonic)
* Selects behavior from internal state
* Controls expression
* Serves the monitoring dashboard on port 8080
* Future long-term memory host

Sensor / Body Node

ESP32-S3 DevKit

Current sensor:

* Photoresistor

Current emitter:

* Onboard RGB LED

Communication:

* USB serial connection between ESP32 and Raspberry Pi

Wiring:

* Photoresistor on GPIO 4, onboard RGB LED on GPIO 38
* Full pin table, voltage divider, and diagram: Hardware/wiring.md

Current Architecture

Photoresistor
↓
ESP32 (body)
↓
USB Serial
↓
Python Collector (Pi 3)
↓
Memory Windows (3s / 10s / 60s)
↓
Cell Network (arousal / fatigue / tonic)
↓
Behavior Engine (sleep / startle / nominal)
↓
USB Serial
↓
ESP32
↓
Onboard LED

Side branch (Pi 3):

Collector
↓
creature_state.json + creature_state_log (SQLite)
↓
Dashboard server (port 8080)
↓
Browser on Mac or phone

Remote dashboard publishing branch:

Collector
↓
creature_state.json + creature_state_log (SQLite)
↓
Dashboard static export
↓
SSH / rsync
↓
Basic Chaos VPS
↓
https://basicchaos.com/creature/

The ESP acts as the body.

The Raspberry Pi 3 acts as the mind.

The Basic Chaos VPS acts only as an external observation surface. It receives a static mirror of the dashboard. It does not control the Creature and cannot connect back to the Pi.

Design Philosophy

ESP Responsibilities

The ESP should remain simple and stable.

Responsibilities:

* Read sensors
* Stream raw data
* Receive commands
* Control hardware outputs

The ESP should not:

* Store memories
* Make decisions
* Interpret meaning
* Run expression logic

Example:

Sensor Input
→ Raw Value
→ Send To Python

and

LED:120
→ Set LED Brightness

Nothing more.

Python Responsibilities

Python contains the changing system.

Responsibilities:

* Data collection
* Memory
* Interpretation
* Expression
* Future learning systems

This allows sensors and emitters to remain stable while behavior evolves.

Current Repository Structure

Creature/

Archive/

* Previous experiments

Code/

  Firmware/

    esp-creature-core/

      src/

        main.cpp

      platformio.ini

  Python/

    collector/

      collector.py

    mind/

      cell_network.py

      behavior.py

      expression_state.py

    dashboard/

      server.py

      index.html

      export_static.py

      sync_to_vps.sh
      creature31may2026.jpg
      public/

    body/

      onboard_led.py

    data/

      creature_raw_light.db

Hardware/

Obsidian/

instructions.md

Current Components

collector.py

Purpose:

Primary runtime process.

Responsibilities:

* Read serial data from ESP
* Save raw events into SQLite
* Maintain rolling memory windows (3s / 10s / 60s, computed in-process)
* Step the cell network each reading (arousal, fatigue, tonic)
* Run the behavior engine to choose the LED action
* Send commands back to ESP
* Write creature_state.json each loop (live snapshot for the dashboard)
* Log a creature_state_log row once per second (history for the dashboard)
* Periodically publish the mirrored dashboard to the Basic Chaos VPS as a non-blocking output channel

This is currently the main Creature process.

Memory windows

Rolling windows are now computed inside collector.py (build_memory_from_window), not a separate file.

Current windows:

* 3 second window
* 10 second window
* 60 second window

Outputs:

* short_mean_3s
* medium_mean_10s
* long_mean_60s
* comparison values between windows (short_vs_long, medium_vs_long)

cell_network.py

Purpose:

The creature's nervous system. A small graph of cells, each holding one value
between 0 and 1. Cells receive signals from sensors and from each other through
weighted links (positive = excitatory, negative = inhibitory). Update is
synchronous: every cell's next value is computed from the previous values, then
all commit at once.

Current cells:

* arousal: fast activation, driven by novelty. Carries the slow "structure" sensitivity that grows over time.
* fatigue: builds slowly from sustained arousal, and inhibits arousal back down (habituation).
* tonic: smoothed ambient light, a rough day/night sense.

External inputs each step: novelty (how much the light changed) and light (current ambient).

Output: a state vector, e.g. {arousal: 0.42, fatigue: 0.10, tonic: 0.29}.

The cell constants (decay, gain) are per-reading, so their real speed depends on
the sensor reading rate. Tune them live on the Pi while watching the dashboard.

behavior.py

Purpose:

The action layer. Reads the state vector and picks one behavior by priority.
State (how the creature feels) is kept separate from action (what it does).

Priority:

1. sleep: if fatigue is high. Dim, slow pulse. Overrides the rest.
2. startle: if arousal is high. Bright, fast pulse.
3. nominal: otherwise. Calm pulsing, brightness following ambient light.

Output:

* behavior (sleep / startle / nominal)
* brightness
* pulse_delay
* direction (rising / falling / steady)
* the state vector (arousal, fatigue, tonic) passed through for logging

expression_state.py

Purpose:

Helper used by the behavior engine. Holds map_range (scales a value from one
range to another) and the older pressure-to-expression mapping kept for
reference. No longer the main expression path.

dashboard (server.py + index.html)

Purpose:

Monitoring view for the Creature, served from the Pi 3 and mirrored to Basic Chaos for remote observation.

server.py:

* Standard-library web server, no extra dependencies
* Serves the dashboard page on port 8080
* Reads creature_state.json (live) and creature_state_log (history)
* Serves the local Creature image at /creature31may2026.jpg

index.html:

* Live light value, min/max, reading rate
* Arousal, fatigue, and tonic shown as bars (structure and the learned novelty weight noted below them)
* Current behavior and output (direction, desired vs sent brightness, pulse)
* Light and memory-window chart
* Internal state over time (arousal, fatigue, tonic)
* Learned reactivity over time (the plastic novelty to arousal weight)
* System health (DB path, row counts)
* Creature image panel using creature31may2026.jpg
* Dual data mode: local Pi dashboard reads API routes, Basic Chaos mirror reads static JSON files

Opened from a browser on the Mac or phone at http://<pi-ip>:8080

Remote mirror:

https://basicchaos.com/creature/

export_static.py:

* Converts the live dashboard into static files
* Copies index.html into dashboard/public/
* Copies creature31may2026.jpg into dashboard/public/
* Writes state.json, history.json, brain.json, and health.json

sync_to_vps.sh:

* Runs the static export
* Uploads dashboard/public/ to the Basic Chaos VPS using rsync over SSH
* Uses the restricted VPS user creature, not root

onboard_led.py

Purpose:

Simple test utility for sending LED commands to the ESP.

Used for development and testing.

Normally not run alongside collector.py because both require access to the same serial port.

Remote Dashboard Publishing

The Creature dashboard is now published outside the local network.

The Raspberry Pi remains the source of truth. The VPS only hosts a mirrored copy of the dashboard for remote viewing.

Architecture:

Collector
↓
creature_state.json + creature_state_log
↓
Dashboard Export
↓
SSH / rsync
↓
Basic Chaos VPS
↓
https://basicchaos.com/creature/

The Raspberry Pi is never exposed directly to the internet.

No inbound connections are allowed to the Pi.

The Pi initiates outbound SSH connections to the VPS and uploads a static copy of the dashboard.

This keeps the Creature private while allowing observation from anywhere.

Dashboard Export

New dashboard component:

dashboard/export_static.py

Purpose:

Converts the live dashboard data into static files suitable for web hosting.

Outputs:

* index.html
* creature31may2026.jpg
* state.json
* history.json
* brain.json
* health.json

These files are written into:

dashboard/public/

The VPS serves these files directly.

Local and Remote Dashboard Modes

The same index.html is used locally and remotely.

Local mode:
* Runs from the Pi dashboard server at http://<pi-ip>:8080
* Reads live data through API routes (/api/state, /api/history, /api/health, /api/brain)
* Loads the Creature image from /creature31may2026.jpg served by server.py

Remote mode:
* Runs from https://basicchaos.com/creature/
* Reads static files uploaded by sync_to_vps.sh
* Loads creature31may2026.jpg from the mirrored dashboard folder

The dashboard chooses its mode automatically by checking whether it is running on basicchaos.com.

Dashboard Publishing

New component:

dashboard/sync_to_vps.sh

Purpose:

Publishes the dashboard mirror to the Basic Chaos VPS.

Process:

1. Export static dashboard files.
2. Upload files using rsync over SSH.
3. Replace the previous dashboard state on the VPS.

The publishing account is a restricted VPS user named:

creature

This user only has access to:

/path/on/server/creature/

The Creature does not use root access for publishing.

Security Model

The VPS is public.

The Raspberry Pi is private.

Connections flow only in one direction:

Pi
→ VPS

The VPS cannot initiate communication with the Pi.

No port forwarding is required.

No public dashboard service runs on the Pi.

This allows remote observation without exposing the Creature runtime or home network.

Dashboard As Expression

The dashboard is now treated as an output channel.

The dashboard is not part of sensing, memory, or decision-making.

It is an observer of internal state.

Current output channels:

* Onboard LED
* Dashboard mirror

Future output channels may include:

* Audio
* Motion
* Displays
* Network messages

Collector Integration

Dashboard publishing is now integrated into the main runtime loop.

The collector periodically triggers dashboard publication without blocking sensor processing.

This is treated as a non-blocking expression process.

The Creature continues sensing and behaving even if dashboard publication fails.

This keeps observation separate from cognition while allowing the outside world to monitor the Creature continuously.

Runtime Notes

A BrokenPipeError may occasionally appear in the dashboard server logs when a browser refreshes, closes, or cancels a request while the Pi is still sending data. This is normally harmless and does not stop the dashboard server.

Current Memory System

The system maintains two layers: rolling memory windows (temporary) and the cell network (internal state).

Rolling windows are short-lived snapshots of recent light. The cell network sits on top of them and holds state that persists across readings.

Short Memory

3 seconds

Represents:

Immediate experience.

Medium Memory

10 seconds

Represents:

Recent experience.

Currently collected but only lightly used.

Long Memory

60 seconds

Represents:

Current environmental baseline.

Arousal

Fast internal activation.

Represents:

How stimulated the creature is right now. Novelty (short_vs_long) pushes it up. It decays quickly toward zero. It also carries the slow "structure" value, a sensitivity that grows over time so a well-exercised creature reacts more strongly. This is the closest thing the system has to growth.

Fatigue

Slow build from sustained arousal.

Represents:

Habituation. While arousal stays high, fatigue rises and pushes arousal back down. This stops the creature from staying excited forever under constant stimulation. It decays slowly once things calm.

Tonic

Smoothed ambient light.

Represents:

A rough sense of how bright the environment is overall, a first step toward a day/night signal.

Current Behavior Model

State and action are separate. The cell network produces a state vector. The behavior engine reads it and picks one behavior.

State (from the cell network):

arousal, fatigue, tonic

Action selection (behavior engine, by priority):

Fatigue high
→ Sleep (dim, slow pulse, overrides the rest)

Arousal high
→ Startle (bright, fast pulse)

Otherwise
→ Nominal (calm pulse, brightness tracks ambient light)

In all behaviors:

Ambient light (latest reading)
→ Base LED brightness

Arousal
→ Pulse speed

short_vs_long
→ Direction (rising / falling / steady)

So the creature reacts to change through arousal, calms itself through fatigue, and its brightness tracks the absolute light level.

Current Observed Behavior

Example:

Stable room lighting
→ LED blinks slowly

Light suddenly changes
→ LED blinks rapidly

Environment stabilizes
→ LED gradually returns to slower blinking

Because movement is based on a 3-second memory window, reactions have a small delay.

This is expected.

Planned Expression Model

Current model:

Cell network (arousal / fatigue / tonic) into a behavior engine.

Planned extension (more sensory layers feeding the network):

Raw vs 3s
→ Reflex

3s vs 60s
→ Adaptation

60s
→ Stasis

Meaning:

Reflex
= Immediate response

Adaptation
= Recent trend

Stasis
= Current world state

This creates multiple behavioral layers.

Long-Term Memory Concept

Rolling memory windows are temporary. The cell network persists across restarts: the arousal cell's structure, its long-run activity average, and the connection weights are saved to creature_brain_state.json (every 60 seconds, on clean shutdown, and via an atexit handler) and reloaded on boot. Fast values (arousal, fatigue, tonic) are deliberately not saved, so the creature wakes calm but keeps its slow self.

Plasticity is now active. The novelty to arousal connection is a learning weight, not a constant. Homeostatic plasticity slowly adjusts it so arousal's long-run activity drifts toward a target (about 0.15). A busy, stimulating environment pushes the weight down, so the creature learns to stay calm and hard to startle. A quiet environment pushes it up, so the creature stays sensitive. The weight is bounded (0.2 to 2.0) so it cannot run away, and because it persists, two creatures with different histories diverge into different temperaments over time. This is the first real long-term change: experience shaping the wiring, not just the moment.

Long-term vision:

The system stores experience as weighted vectors.

Important experiences become persistent.

Unimportant experiences decay.

Conceptually:

Experience
→ Pressure
→ Memory Weight
→ Long-Term Storage

The goal is not to preserve all raw data.

The goal is to preserve meaningful experience.

Future Sensors

Possible future sensors:

* Light
* Sound
* Temperature
* Distance
* Motion
* Time
* Network activity
* Internal state signals

All sensors should follow the same pattern:

Sensor
→ Raw Data
→ Python Interpretation

Future Emitters

Possible emitters:

* LEDs
* Speaker
* Tone generator
* Motors
* Displays
* Servo movement

All emitters should follow:

Python Decision
→ Command
→ ESP Execution

Current Development Status

Completed:

* Git workflow
* ESP firmware framework
* Raspberry Pi 3 runtime
* Serial communication
* SQLite storage
* Memory windows
* Cell network (arousal, fatigue, tonic)
* Behavior engine (state separated from action)
* First emitter
* LED feedback loop
* Monitoring dashboard (served from the Pi 3)
* Remote dashboard publishing to Basic Chaos
* Secure VPS synchronization through a restricted creature user
* External observation channel
* Creature image panel in local and remote dashboard
* Dual local/API and remote/static dashboard mode

Working demonstration:

Light Sensor
→ Memory
→ Cell Network
→ Behavior
→ LED Response

This is the first complete perception-to-expression loop in the Creature architecture.

The system is now capable of:

* Sensing
* Remembering over multiple timescales
* Building internal state (arousal, fatigue, tonic)
* Selecting behavior by priority
* Expressing state through a physical output
* Being monitored live through the local dashboard
* Publishing a remote dashboard mirror without exposing the Pi to the internet
* Showing the physical Creature setup alongside its live internal state

Core Architectural Principle

The Creature is divided into two parts:

Body (ESP32)

* Reads sensors
* Controls hardware
* Executes commands

Mind (Python on Raspberry Pi)

* Stores memory
* Interprets experience
* Generates behavior
* Controls expression

The body should remain simple.

The mind should remain changeable.

This separation allows the Creature to evolve without constantly rewriting the hardware layer.