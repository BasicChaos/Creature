Creature Theory and Background

Version: May 30 2026

What this document is

This is the theory behind the Creature. It collects the ideas the project borrows from and shows where each one lives in the code. It is not a literature review. It is a working map: enough background to understand why the system is built the way it is, and enough pointers to read deeper when you want to.

The project does not invent new science. It assembles old, well-tested ideas from cybernetics, neuroscience, and behavior-based robotics into a small physical organism. The value is in the assembly, not the parts.

A note on the parts you already have:

* Body: ESP32, light sensor, LED.
* Mind: a cell network, a behavior engine, memory windows, persistence, and one plastic connection.
* Observation: the dashboard.

Keep those in mind as you read. Each theory below points back to one of them.


1. The stance: an organism, not an assistant

Most AI work builds tools that answer. This project builds a thing that lives: it senses, holds internal state, acts, and continues across time. That framing comes from two fields.

Artificial Life (Alife) studies life as a process that can run in any medium, not only in biology. The question is not "is it intelligent" but "does it behave like something alive." A slow-growing, self-maintaining system counts, even a simple one.

Embodied and enactive cognition argues that a mind is not a program running on abstract symbols. It is shaped by having a body in a world. Cognition is something a creature does through acting, not something it computes in isolation. Francisco Varela, Evan Thompson, and Eleanor Rosch laid this out in The Embodied Mind (1991). Humberto Maturana and Varela's idea of autopoiesis (a system that continuously produces and maintains itself) sits underneath it.

In the Creature: the strict split between body (ESP, no logic) and mind (Pi) is the embodiment claim made concrete. The creature has no goals typed into it. Its behavior comes from the loop between sensor, state, and output.


2. Cybernetics and homeostasis

Cybernetics is the study of control and communication through feedback, in machines and animals alike. Norbert Wiener named it in Cybernetics (1948). The core move is circular causality: output feeds back and changes future input, so cause and effect form a loop rather than a line.

W. Ross Ashby took this furthest for our purposes. In Design for a Brain (1952) he built the homeostat, a machine that holds its essential variables inside survivable limits by reorganizing itself when pushed too far. He called this ultrastability: not just resisting disturbance, but changing internal structure to stay viable.

In the Creature: the feedback loop is the whole pipeline. Light changes the state, state changes the LED, and over longer time the homeostatic weight reorganizes the wiring to keep arousal within bounds. That last part is Ashby's ultrastability in miniature.


3. The cell as a leaky integrator

The Creature's cells are not biological neurons and not deep-learning units. They are rate-based leaky integrators, the oldest and simplest useful neuron model. Each cell holds one value. Every step it leaks a little (decay) and takes in a little (gain times input). Left alone it fades toward zero. Driven steadily it settles at a level set by the balance of drive and decay.

This is the continuous-time recurrent neural network (CTRNN) family. Randall Beer used these to study minimal cognition: how very small networks of leaky cells can produce adaptive behavior. The appeal is that the dynamics are transparent. You can reason about a cell with two numbers.

In the Creature: NetworkCell.compute is exactly this. activation = activation times decay, plus input times gain, clamped to a range. The decay and gain constants set how fast each cell reacts and how long it remembers.


4. Few cells, rich behavior

Valentino Braitenberg's Vehicles (1984) is the key intuition. He showed that vehicles with two or three sensors wired directly to motors produce behavior an observer would call fear, aggression, or love, with no internal complexity at all. The behavior is in the coupling, not in any single part.

He also gave a warning that matters for this project: the law of uphill analysis and downhill synthesis. Building a behaving system from simple parts is easy. Looking at a behaving system and inferring its parts is hard. A creature can look far smarter than its wiring.

In the Creature: three cells and four connections produce startle, habituation, and calm. None of those behaviors is coded directly. They fall out of how the cells push and pull on each other.


5. Arousal: tonic and phasic

Real nervous systems separate the background level of activation from the sharp response to events. The slow background is tonic. The fast spike is phasic. Neuromodulators like noradrenaline track novelty and surprise, raising arousal when something changes and letting it fade when the world is steady.

Novelty here is relative, not absolute. The system reacts to change against a baseline, not to the raw value. This is why a bright room is not exciting once you are used to it, but a sudden change is.

In the Creature: the arousal cell is phasic, it spikes and fades. The tonic cell is the slow background, a smoothed sense of ambient light. Novelty is computed as the short-term average against the long-term average, so it measures change, not level.


6. Habituation and sensitization

These are the two simplest forms of learning, and they appear in animals with only a handful of neurons. Eric Kandel won a Nobel for tracing them in the sea slug Aplysia, whose gill-withdrawal reflex weakens with repeated harmless touch (habituation) and strengthens after a sharp stimulus (sensitization).

Habituation is the more important one for a creature that should not panic forever. If a stimulus keeps coming and nothing bad follows, the response should fade. This is not fatigue in the everyday sense. It is the nervous system learning that a signal is not worth reacting to.

In the Creature: the fatigue cell produces habituation directly. Sustained arousal builds fatigue, and fatigue inhibits arousal. The creature reacts to a change, then stops reacting even if the change continues. Sensitization shows up in the structure value, which slowly raises sensitivity with use.


7. Plasticity: Hebbian and homeostatic

Plasticity is the brain changing its own wiring with experience. There are two broad kinds, and the difference shaped a real decision in this project.

Hebbian plasticity is the famous one. Donald Hebb, in The Organization of Behavior (1949), proposed that cells that fire together strengthen their connection. "Cells that fire together wire together" is the slogan. It explains association and learning, but on its own it is unstable: strong connections make cells fire more, which strengthens the connection further, which can run away.

Homeostatic plasticity is the counterweight. Gina Turrigiano showed that neurons defend a target activity level by scaling their inputs up or down. If a neuron is too active for too long, it weakens its inputs. If too quiet, it strengthens them. Her phrase is "the self-tuning neuron" (2008). This is stable by design, because it always pushes back toward the target.

We chose homeostatic plasticity for the first learning step. The reason is practical as much as theoretical: a homeostatic rule is bounded and self-correcting, so it cannot spiral into a broken state that is hard to debug. A raw Hebbian rule can.

In the Creature: the novelty to arousal connection is plastic. Its weight drifts to keep the arousal cell's long-run activity near a target. A busy environment lowers the weight, so the creature learns to stay calm. A quiet environment raises it, so the creature stays sensitive. Two creatures with different histories grow different temperaments. Because the weight persists to disk, that difference accumulates.


8. Allostasis: stability through change

Homeostasis keeps a variable near a fixed point. Allostasis, a newer idea from Peter Sterling and Joseph Eyer (1988, and Sterling's 2012 model of predictive regulation), says living systems do something subtler: they change their set points to anticipate need, achieving stability through change rather than stability at a point. The body prepares for the day rather than just correcting errors.

This matters for the long arc of the project. A creature that only corrects toward fixed targets stays the same forever. A creature that shifts its targets based on its history develops.

In the Creature: the persisted structure and the slowly moving learned weight are the first hints of allostasis. The creature is starting to carry its past into how it regulates the present. Step 3, redefining novelty against long-term memory, pushes further in this direction.


9. Action selection: subsumption

Having internal state is not enough. The creature must choose what to do. Rodney Brooks, building mobile robots in the 1980s, rejected the idea that a robot needs a central model of the world and a planner. In A Robust Layered Control System (1986) and Intelligence Without Representation (1991) he proposed subsumption: layers of simple behaviors, each tied directly to sensing, where higher-priority layers can override lower ones. The intelligence is layered and reactive, not centralized.

In the Creature: the behavior engine is a small subsumption stack. Sleep overrides startle, startle overrides nominal. There is no planner and no world model. Each behavior reads the state and competes by priority. State (the cells) and action (the behavior engine) are kept separate on purpose, which is what lets new behaviors slot in without rewiring the feelings.


10. Memory across timescales

Brains do not have one memory. They have several, running at different speeds: a fast trace of the immediate moment, a slower sense of recent context, and slow consolidation of what mattered into something lasting. Sleep and offline replay move information from fast stores to slow ones.

In the Creature: the memory windows (3, 10, 60 seconds) are the fast and medium traces. The pressure and structure values are slower. Persistence to disk is the slow store: the creature's slow self survives a restart, while its fast state resets, much like waking up. The fast values are deliberately not saved, because a creature should wake calm, not mid-panic.


11. Where this is heading: novelty as prediction

The biggest idea still ahead is predictive processing. Karl Friston's free energy principle, and the broader predictive coding view, frame the brain as a prediction machine. Perception is the system's best guess about its causes, and what reaches awareness is mostly prediction error: the gap between what was expected and what happened. Novelty, in this view, is surprise, measured against a learned model of the world.

The Creature's current novelty is short-sighted. It only compares the last few seconds to the last minute. True novelty would be measured against everything the creature has ever experienced: have I seen this before. That requires a stored, compressed model of the past and a familiarity signal read against it. This is the planned step 3, and predictive processing is the theory that frames it.


12. Concept to code map

* Embodiment, body and mind split: the ESP (body) and Pi (mind) separation.
* Cybernetic feedback: the full sensor to state to LED loop.
* Ultrastability (Ashby): the homeostatic weight reorganizing to keep arousal viable.
* Leaky integrator / CTRNN (Beer): NetworkCell, with decay and gain.
* Behavior from coupling (Braitenberg): three cells and four links producing startle and calm.
* Tonic vs phasic arousal: the tonic cell (background) vs the arousal cell (spike).
* Habituation (Kandel): the fatigue cell inhibiting arousal under sustained input.
* Sensitization: the structure value rising with use.
* Hebbian vs homeostatic plasticity (Hebb, Turrigiano): the plastic novelty to arousal weight, using the homeostatic rule.
* Allostasis (Sterling): persisted structure and learned weight carrying history forward.
* Subsumption (Brooks): the behavior engine, sleep over startle over nominal.
* Multiple memory timescales and consolidation: memory windows plus persistence.
* Predictive processing (Friston): the planned step 3, novelty as prediction error.


13. Further reading

* Norbert Wiener, Cybernetics (1948).
* W. Ross Ashby, Design for a Brain (1952).
* Donald Hebb, The Organization of Behavior (1949).
* Valentino Braitenberg, Vehicles: Experiments in Synthetic Psychology (1984).
* Rodney Brooks, Intelligence Without Representation (1991).
* Francisco Varela, Evan Thompson, Eleanor Rosch, The Embodied Mind (1991).
* Eric Kandel, In Search of Memory (2006), for habituation and sensitization in Aplysia.
* Gina Turrigiano, The Self-Tuning Neuron: Synaptic Scaling of Excitatory Synapses (2008).
* Peter Sterling, Allostasis: A Model of Predictive Regulation (2012).
* Randall Beer, work on continuous-time recurrent neural networks and minimal cognition.
* Karl Friston, writing on the free energy principle and predictive coding, for the road ahead.

These are starting points, not a syllabus. Read the ones that match the part of the Creature you are working on next.
