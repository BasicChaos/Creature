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
| 6    | `fuelgauge`| MAX17048 power level  | needs LiPo on its JST; cell 3.0-4.2V, percent 0-100, stable |

Step 6 is the untethered power sense and runs only after the battery is wired.
The MAX17048 is powered by the cell through its JST port, so with no LiPo plugged
in it will not appear on I2C at all. Its VIN pin goes to 3V3, never to the cell: on
the Adafruit breakout VIN only sets the I2C pull-up voltage. See the PowerBoost
add-on in WIRING v06 for the full battery wiring.

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

## Air senses and e-paper (bare board)

`env:air` tests three new parts on a bare ESP32-S3 (N16R8) on a breadboard, away
from the v06 body: the SCD4x (CO2, temperature, humidity), the SGP41 (VOC and
NOx) and the Waveshare 2.13inch e-Paper HAT V4. Each part is reported on its own,
so one missing part does not stop the other two.

| Part    | Part pin (wire)  | ESP32-S3 |
|---------|------------------|----------|
| SCD4x   | VDD              | 3V3      |
| SCD4x   | GND              | GND      |
| SCD4x   | SDA              | GPIO 8   |
| SCD4x   | SCL              | GPIO 9   |
| SGP41   | VIN              | 3V3      |
| SGP41   | GND              | GND      |
| SGP41   | SDA              | GPIO 8   |
| SGP41   | SCL              | GPIO 9   |
| e-paper | VCC (grey)       | 3V3      |
| e-paper | GND (brown)      | GND      |
| e-paper | DIN (blue)       | GPIO 1   |
| e-paper | CLK (yellow)     | GPIO 2   |
| e-paper | CS (orange)      | GPIO 42  |
| e-paper | DC (green)       | GPIO 41  |
| e-paper | RST (white)      | GPIO 40  |
| e-paper | BUSY (purple)    | GPIO 39  |

Leave the SGP41 pin marked `3.3V` open; it is an output. The HAT's BS switch must
sit at 0 (4-line SPI). Both sensor boards carry their own I2C pull-ups. None of
these pins are used by the v06 body, and the addresses (0x62, 0x59) do not clash
with its I2C bus, so the wiring can carry over.

Pass when:

- SCD4x: first reading after about 5 s; CO2 400-2000 ppm indoors, and it climbs
  within a few readings when you breathe on it.
- SGP41: self-test ok; raw VOC in the tens of thousands, dropping when you
  breathe on it. The VOC index reads 0 for about 45 s, then settles near 100. The
  NOx index needs several minutes and settles near 1.
- e-paper: a full refresh takes roughly 1 to 5 s and the readings show. It
  redraws every 15 s with a fast partial refresh; every tenth redraw is a full
  one, which clears the ghosting that partial refreshes leave. A refresh that
  returns at once, or `Busy Timeout!` on serial, means wiring.

## Audio: playing clean tones (important)

**Root cause + fix (confirmed 22 Jun 2026):** the persistent distortion was not a
software/timing problem. Amp VIN was wired to 3V3, which is not enough headroom —
it clipped at real signal levels. Moving VIN to the +5V rail removed the
distortion completely. See `WIRING v06.md` → Audio for the pin change.

The recipe below is still worth keeping — it fixes click artifacts at start/stop,
a separate issue from clipping. This took a long debug to get right. The
reference is `playTone` + `ampSilence` in `src/smoke_test.cpp`. Reuse this recipe
in the real v06 firmware; do not rediscover it. Each rule earns its place:

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
- **Mic and amp run concurrently; do not gate the mic (corrected 27 Jun 2026).** Mic
  (I2S0) and amp (I2S1) are separate peripherals on separate pins and run fine at the
  same time. The smoke test below still uninstalls the mic during its tone, harmless
  for a part-test, but the real firmware keeps the mic live. The distortion was amp
  VIN on 3V3 (power), not I2S contention, so the creature hears its own voice.
- **Config that works:** I2S1, BCLK=15/LRC=16/DIN=17, 16 kHz, 16-bit, ONLY_LEFT,
  STAND_I2S, `use_apll=false`, 8x256 DMA, continuous phase across tones. ONLY_LEFT is
  fine (the standalone speaker test proved it). APLL did not help.

VIN is now 5V (full 3W headroom). If that is too loud at default gain, turn it
down with hardware GAIN (GAIN to VIN) rather than shrinking the digital
amplitude. A decoupling cap on VIN (100µF + 0.1µF) is optional extra insurance,
not required for the fix.

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
- `air` env: `zinggjm/GxEPD2` (also pulls Adafruit GFX and BusIO) and
  `sensirion/Sensirion Gas Index Algorithm`. The Sensirion sensors themselves are
  read with raw I2C.
