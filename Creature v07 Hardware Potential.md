# Creature v07 Hardware Potential

Version: v07 hardware potential, June 23 2026. Status: planning note.

This document collects hardware opportunities for the next several Creature
evolutions. It does not replace the v06 wiring document or the v06 rollout plan.
It is a map of what is worth trying, in what order, and why.

The short read: the Creature does not need a pile of new sensors yet. It needs
better anatomy. The strongest next hardware move is to make the existing v06
body physically coherent, close the light and sound loops deliberately, then add
real energy through a battery. After that, touch, haptics, proximity, and limited
movement become worthwhile.

## Starting Point

v06 already has the important shape:

- Four senses: BH1750 light, INMP441 sound, ICM-20689 motion, BME280 weather.
- Two emitters: SK6812 RGBW strip and MAX98357A speaker.
- ESP32-S3 body: read raw sensors, stream JSON, receive commands, render outputs.
- Raspberry Pi mind: normalize, step the field, persist state, send `PIX:` and
  `VOX:`.
- Twelve-cell ring plus fixed reservoir and predictive cells.

That means the hardware question is not "what can we add?" It is "what physical
couplings make the field less dependent on a lucky room?"

The current v06 goal still stands: action must become sensing. The strip should
return through the light sensor. The speaker should return through sound or body
motion. The body should have costs and constraints the field can feel.

## Design Rules

Use these rules before adding hardware:

1. Prefer closed loops over more passive inputs.
2. Prefer physical consequence over richer dashboards.
3. Keep the ESP dumb. It may have safety reflexes, but not cognition.
4. Add raw senses or render-only actuators; interpretation stays on the Pi.
5. Do not add new field anchors casually. Battery, haptics, touch, and proximity
   can often enter as modulation or event channels first.
6. Prove one part on the bench before integrating it into firmware.
7. Do not add locomotion until the body has power, proximity, contact, and a
   hard stop.

## Priority 0: Give v06 a Body

The current hardware is still a bench organism: separated organs on a breadboard.
That is perfect for bring-up, but weak for the loop. The first v07-ish hardware
move should be a small body carrier.

Build a rigid carrier that fixes:

- Strip position.
- BH1750 position and angle relative to the strip.
- Speaker position.
- INMP441 position and acoustic path relative to the speaker.
- IMU position on the body, not loose on the breadboard.
- BME280 position away from direct heat from the ESP, strip, and PowerBoost.
- PowerBoost, battery, and future fuel gauge placement.

Make the light sensor and mic mounts adjustable. The v06 loop should start loose:
slightly off-axis, partially shielded, or physically separated enough that it can
sustain activity without running away.

Useful body details:

- A small black or translucent light well between strip and BH1750.
- A movable flag or shutter to tune how much strip light reaches the sensor.
- A small speaker cavity or baffle.
- A mic tunnel with foam or a partial shield so room sound and self-sound can be
  tuned separately.
- Strain relief for the strip and speaker wires.
- A power switch on PowerBoost EN to GND.

Acceptance:

- A known `PIX:` frame causes a measurable, repeatable lux change.
- A known `VOX:` tone causes a measurable, repeatable mic or IMU response.
- Tapping or moving the body produces a motion response that is not just loose
  wiring.
- Sensor placement can be changed without rewiring.

## Priority 1: Characterize the Existing Loops

Before adding new parts, measure the two loops v06 already claims.

### Light Loop

Test:

- Send fixed strip frames: dark, dim blue, dim white, warm, cool, moving pulse.
- Log raw `light_lux` and normalized `light_norm`.
- Repeat at different BH1750 angles and distances.

Desired result:

- The BH1750 sees the strip reliably.
- Ambient room light does not completely swamp the strip.
- The coupling can be tuned from weak to strong physically.

Hardware idea:

- Add a small adjustable shield or tube around the BH1750.
- Consider a second light sensor only if the single BH1750 cannot both sense the
  room and see the strip. One sensor could face outward, one inward.

### Sound Loop

Current caution: the firmware uninstalls the mic while playing a tone so the amp
can sound cleanly. That is good for audio quality, but it limits direct "hear
self while speaking" feedback.

Good loop options:

- Let the mic hear the tail after the tone ends.
- Use the IMU as a body-conducted voice sensor: speaker vibration returns as
  motion.
- Add a small vibration/contact sensor near the speaker if the IMU is not
  sensitive enough.
- Use a second sound sensor later only if the single I2S path remains too
  constrained.

Desired result:

- `VOX:` output creates some returning signal without having to make the speaker
  loud.
- The returning signal is tunable by speaker mount, cavity, and mic placement.
- The Creature can be quiet in the room while still sensing its own voice.

## Priority 2: Battery as Real Metabolism

This is the strongest v07 hardware addition.

Use a single LiPo cell, PowerBoost/load-sharing charger path, and a MAX17048 fuel
gauge on the existing I2C bus. The fuel gauge reads raw cell voltage and state of
charge, then the ESP streams:

```json
{"battery_pct": 84.2, "battery_v": 4.05}
```

Why this matters:

- The field already has energy, rest, sleep, and learning cost.
- Right now that metabolism is simulated while the body has unlimited USB power.
- Battery makes scarcity physical.
- Expression becomes costly: strip brightness and voice are no longer free.
- Feeding the body by plugging it in becomes a real event.

Hardware:

- MAX17048 fuel gauge, I2C address `0x36`.
- VIN to raw battery positive, not 3V3 and not boosted 5V.
- GND to common ground.
- SDA/SCL to GPIO 8/9.
- Protected LiPo cell or charger with undervoltage protection.
- PowerBoost EN switch for physical off.

Firmware:

- Add `ENABLE_BATTERY`.
- Stream `battery_pct` and `battery_v`.
- Add body-level safety reflexes:
  - Below warn threshold, cap strip brightness first.
  - Below critical threshold, stop outputs and deep-sleep the ESP.
  - Keep this on the body because it protects hardware.

Mind:

- Parse and log battery.
- Show it on the dashboard.
- Scale field energy income by state of charge.
- Optionally create a small event when charge drops sharply under load.

Acceptance:

- I2C scan sees `0x36` with the other v06 devices.
- Battery percent and voltage are stable and plausible.
- Unplugging power causes a gradual state-of-charge decline.
- Low charge makes the Creature dimmer, quieter, more restful, and more likely
  to sleep without corrupting slow state.

## Priority 3: Haptics Before Locomotion

Haptics are movement without the danger and complexity of mobility. They are a
very good fit for this Creature.

Candidate:

- DRV2605L haptic controller.
- I2C default address `0x5A`.
- Drives ERM vibration motors or LRA haptic actuators.

Why it fits:

- The Pi can send a render-only command such as `HAP:intensity,ms,effect`.
- The body vibrates.
- The IMU senses the vibration.
- That creates a physical action-sense loop without wheels.
- It gives the Creature a fidget, startle, shiver, purr, or pulse.

Important address note:

- DRV2605L default `0x5A` conflicts with the default MPR121 touch controller.
- If both are added, pick breakouts or address wiring that avoid the clash.

Protocol idea:

```text
HAP:effect,intensity,ms
```

Start simpler if needed:

```text
HAP:intensity,ms
```

Acceptance:

- A haptic command produces a visible IMU motion response.
- Different effects or durations produce distinguishable motion signatures.
- Haptic output does not brown out the ESP or corrupt I2C.
- Haptics can be disabled independently with an environment or compile flag.

## Priority 4: Touch and Skin

Touch is probably the next best sense after battery and haptics because it gives
the Creature contact with a person and a body boundary.

Candidate:

- MPR121 capacitive touch controller.
- 12 electrodes over I2C.
- Default address `0x5A`, configurable on many breakouts to `0x5B`, `0x5C`, or
  `0x5D`.

Possible skin:

- Copper tape pads under paper, fabric, acrylic, or thin printed shell.
- One pad near the light sensor.
- One pad near the speaker.
- Several along the strip.
- One "hold" pad near the body base.

How to feed the field:

- Start as an event channel, not a new anchor.
- Derive values such as `touch_any`, `touch_count`, `touch_region`, and
  `touch_delta`.
- Later, if touch proves central, add a dedicated ring anchor.

Why it matters:

- Touch has meaning at organism scale.
- It gives a non-room-dependent source of surprise.
- It lets human interaction become physical, not just observational.

Acceptance:

- Touch is stable across humidity and power states.
- Idle touch does not chatter.
- A hand, tap, or hold creates distinct event patterns.
- The field reacts without saturating or staying permanently aroused.

## Priority 5: Near-Field Presence

Presence is useful, but less important than battery, haptics, and touch.

Candidate options:

- VL53L1X time-of-flight distance sensor, I2C address `0x29`, roughly near-field
  to several meters depending on target and conditions.
- APDS9960 proximity, RGB color, ambient light, and gesture sensor. Useful if the
  Creature should sense color or near motion around the face.

Use cases:

- Someone approaches.
- Something blocks the Creature's face.
- The Creature can point a sensor head and tell whether the world changed.
- Strip color can be sensed as color, not only brightness, if using APDS9960.

How to feed the field:

- Start with a scalar `presence` or `distance_change`.
- Treat sudden approach as event pressure.
- Avoid making it a permanent fifth/sixth anchor until the v06 ring has proven it
  survives nights with the existing four senses.

Acceptance:

- Stable reading at the intended body scale.
- Room geometry does not keep it permanently high.
- Approach and retreat are distinguishable.
- It still works with the strip and speaker active.

## Priority 6: One Small Motor

Do not start with locomotion. Start with orientation.

Best first motor:

- One micro servo that moves a sensor, a light shield, a speaker baffle, or a
  small head.

Why:

- It creates action with consequence.
- It changes what the sensors read.
- It is easier to make safe than wheels.
- It lets the Creature probe the world without roaming.

Good first mechanisms:

- Light sensor eyelid/shutter.
- Strip-to-sensor coupling vane.
- Speaker baffle.
- Head pan with BH1750 or ToF.
- Tiny body rock that the IMU can feel.

Servo control:

- Direct PWM from an unused safe GPIO if only one servo is used.
- PCA9685 I2C servo driver later if multiple servos are used.
- Power servos from a separate 5V rail with common ground, not from the ESP 3V3.

Protocol idea:

```text
MOV:channel,position,speed
```

Acceptance:

- Motion changes at least one sensor reading.
- The IMU can distinguish self-motion from stillness.
- Servo current does not reset the ESP.
- Motion has physical limits and cannot grind.

## Later: Mobility

Actual locomotion should wait.

Requirements before wheels or tracks:

- Battery and fuel gauge working.
- Low-battery reflex working.
- Touch or bump sensing.
- Distance/proximity sensing.
- Hard power switch.
- Software command timeout.
- Slow speed limit.
- Physical test space.

Candidate:

- DRV8833 motor driver for two small DC gear motors or one stepper.

Why to wait:

- Locomotion changes risk class.
- It creates cable, power, collision, and runaway problems.
- A Creature that can shiver, turn its head, and sense touch is already embodied
  enough to learn from action.

## Candidate Hardware Summary

| Category | Candidate | Bus / pins | Why it matters | Priority |
|----------|-----------|------------|----------------|----------|
| Body geometry | Chassis, baffles, shields | mechanical | Turns breadboard parts into anatomy | now |
| Battery | MAX17048 + LiPo + PowerBoost | I2C `0x36`, power | Makes metabolism physical | v07 |
| Haptics | DRV2605L + ERM/LRA | I2C `0x5A` | Safe movement loop through IMU | v07+ |
| Touch | MPR121 + copper pads | I2C `0x5A-0x5D` | Contact, holding, skin | v07+ |
| Presence | VL53L1X | I2C `0x29` | Approach/retreat, near world | later |
| Color/proximity | APDS9960 | I2C | Sees color/gesture/proximity | later |
| Orientation | Micro servo | PWM or PCA9685 | Moves sensor or body part | later |
| Locomotion | DRV8833 + gear motors | GPIO/PWM | Actual movement | much later |

## I2C Address Watchlist

Current v06 bus:

- BH1750: `0x23`
- BME280: `0x76`
- ICM-20689 / MPU family IMU: `0x68`
- Planned MAX17048: `0x36`

Possible additions:

- VL53L1X: `0x29`
- DRV2605L: `0x5A`
- MPR121: default `0x5A`, often configurable to `0x5B`, `0x5C`, `0x5D`

The main conflict to avoid is DRV2605L and MPR121 both at `0x5A`.

## Evolution Path

### v07A: Anatomy and Loops

- Build the body carrier.
- Stabilize sensor and emitter placement.
- Characterize the strip-to-light loop.
- Characterize the speaker-to-mic or speaker-to-IMU loop.
- Keep the field and firmware behavior otherwise unchanged.

Success:

- The Creature's own output reliably returns as input.
- Coupling can be tuned physically.
- Nights show self-sustained activity without runaway.

### v07B: Real Energy

- Add LiPo, PowerBoost/load-sharing path, MAX17048.
- Stream battery percentage and voltage.
- Add low-battery safety reflexes.
- Couple state of charge to field metabolism.

Success:

- Full charge feels different from low charge.
- Expression costs energy.
- Feeding the Creature changes behavior.

### v07C: Haptic Body

- Add DRV2605L and a small vibration actuator.
- Add `HAP:` rendering.
- Let the IMU feel self-vibration.

Success:

- The Creature can shiver or pulse.
- The field can sense the effect physically.
- Haptics become a safe motor primitive.

### v07D: Skin

- Add MPR121 and touch pads.
- Treat touch as event and contact state.

Success:

- Touch, hold, and release create distinct field events.
- Human interaction becomes part of the body loop.

### v08: Orientation

- Add one servo to move a sensor, baffle, eyelid, or head.
- Add `MOV:` rendering with limits.

Success:

- Movement changes sensing.
- The Creature can probe without locomotion.

### v09: Mobility, Maybe

- Only after power, proximity, touch/bump, and safety reflexes exist.
- Start extremely slow.

Success:

- Movement creates meaningful new sensor history without making the system unsafe
  or unobservable.

## First Build Recommendation

The best next build is:

1. Make a small adjustable body carrier.
2. Measure and tune the existing light and sound loops.
3. Add MAX17048 battery sensing.
4. Couple battery to metabolism.
5. Add haptic vibration and close that loop through the IMU.

That path keeps the Creature small, embodied, and testable. It also respects the
v06 lesson: the organism becomes more alive when action returns through the
world, not when the model grows more complicated.
