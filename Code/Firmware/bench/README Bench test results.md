# Creature v06 bench bring-up

Phase 2 of the rollout: prove every part on the bench, one at a time, with the
smallest possible sketch, before the real firmware touches it. A part that fails
here fails in isolation, where it is obvious.

This is a separate PlatformIO project. It does not touch the main firmware in
`../esp-creature-core`. Each part is its own build environment.

## How to run one test

```
pio device list                      # find the port, set it in platformio.ini
pio run -e <env> -t upload           # build + flash
pio device monitor -b 115200         # read the result over serial
```

Only one program can hold the ESP serial port. Do not run the PlatformIO monitor
and the Pi collector at the same time.

## Order and pass criteria

Run in this order. Do not start the next part until the current one passes.

| Step | env        | Part                  | Pass when |
|------|------------|-----------------------|-----------|
| 1    | `i2c_scan` | I2C bus               | 0x23, 0x68, 0x76 all show |
| 2    | `bme280`   | BME280 weather        | plausible temp + pressure, nonzero, stable |
| 3    | `imu`      | ICM-20689 motion      | WHO_AM_I = 0x98; motion scalar rises on tap, settles still |
| 4    | `sk6812`   | SK6812 RGBW strip     | every pixel shows R, G, B, W; no dead/wrong pixels |
| 5    | `speaker`  | MAX98357A + speaker   | clean 440 Hz tone, volume steps up, silent at rest |
| 6    | `fuelgauge`| MAX17048 power level  | needs LiPo on VIN; cell 3.0-4.2V, percent 0-100, stable |

Step 6 is the untethered power sense and runs only after the battery is wired.
The MAX17048 is powered by the cell, so with no LiPo on its VIN it will not appear
on I2C at all. Wire its VIN to the PowerBoost BAT pad (raw cell), not to 3V3. See
the PowerBoost add-on in WIRING v06 for the full battery wiring.

The mic (INMP441) and light sensor (BH1750) are already proven in the v05
firmware, so they are not repeated in the per-part list. If you want to re-check
them alone, the v05 firmware streams `sound_rms` and `light_lux` over serial.

## Combined smoke test

`env:smoke` brings up every part in one flash: it reads BH1750, BME280, and the
IMU over I2C, reads the mic (I2S0), animates the strip, blinks the onboard pixel,
and plays a quiet tone on the amp (I2S1) every five seconds. It prints one status
line per cycle with a PASS/-- per part.

```
pio run -e smoke -t upload && pio device monitor -b 115200
```

Use it for a fast "is the whole node alive" check. It does not replace the
per-part tests: when something looks wrong here, drop back to that part's own env
to isolate it. The mic and amp share this sketch but not pins (mic = I2S0,
amp = I2S1). On boot it runs an R/G/B/W strip proof and a short chirp, then the
status line starts. Tap the board, cover the light sensor, and make noise to see
the motion, light, and mic values move.

## Audio: playing clean tones (important)

This took a long debug to get right. The reference is `playTone` + `ampSilence` in
`src/smoke_test.cpp`. Reuse this recipe in the real v06 firmware; do not rediscover
it. Each rule earns its place:

- **Drive a real signal level** (amplitude ~0.25 of full scale, not tiny). The
  MAX98357A is class-D and sounds scratchy at very low digital levels. To make it
  quieter, turn down the hardware GAIN pin (GAIN to VIN), do not shrink the digital
  amplitude.
- **Fade in and out ~10 ms.** Abrupt edges cause hard clicks at start and finish.
- **Drain the tail, never chop it.** Do not call `i2s_zero_dma_buffer` right after a
  tone; it cuts the audio still in the DMA buffer (end click). Write ~150 ms of
  trailing silence so the faded tail plays out.
- **Warm the amp before the first tone** with ~40 ms of silence, or the first tone
  is distorted (cold start), especially after any I2S driver churn.
- **Give the amp the I2S subsystem to itself during playback.** Mic (I2S0) and amp
  (I2S1) interfere; the smoke test uninstalls the mic during the tone. Principle for
  the real firmware: do not listen while speaking, gate the mic during output.
- **Config that works:** I2S1, BCLK=15/LRC=16/DIN=17, 16 kHz, 16-bit, ONLY_LEFT,
  STAND_I2S, `use_apll=false`, 8x256 DMA, continuous phase across tones. ONLY_LEFT is
  fine (the standalone speaker test proved it). APLL did not help.

For clean tones at any pitch while staying quiet, the hardware option is a
decoupling cap on the amp VIN (100µF + 0.1µF) and/or VIN at 5V with lower gain.

## Notes carried from WIRING v06

- I2C is SDA=8, SCL=9. The GY-302 (BH1750) and GY-521 (IMU) carry their own
  pull-ups, so no external I2C resistors are needed.
- The IMU reports `WHO_AM_I = 0x98`. It is an ICM-20689, not a strict MPU-6050.
  This test reads raw registers so it does not depend on a driver library. When
  you add the IMU to the firmware, use the `finani/ICM20689` library.
- BME280 is at 0x76 (SDO to GND). If `begin()` fails, try 0x77.
- The SK6812 is the only 5V load. Keep brightness low off USB and avoid
  all-channels-white to prevent a brownout/reset. Confirm the 470 ohm data
  resistor and the 1000 uF cap are in place.
- Mic and amp use separate I2S peripherals (mic = I2S0 in the firmware, amp =
  I2S1 here), so they never share pins.

## Libraries

`pio run` pulls these automatically from `platformio.ini`:

- `bme280` env: `adafruit/Adafruit BME280 Library` (also pulls Adafruit Unified
  Sensor and BusIO).
- `sk6812` env: `adafruit/Adafruit NeoPixel`.
- `i2c_scan`, `imu`, `speaker`: no external library.
