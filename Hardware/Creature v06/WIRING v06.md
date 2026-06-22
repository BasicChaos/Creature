# Creature Hardware Wiring

Version: v06 — June 19 2026

Body node wiring for v06. The devices below match the live firmware in
`Code/Firmware/esp-creature-core/src/main.cpp`. The ESP reads the four senses,
accepts legacy `LED:<brightness>` status commands, and accepts v06 `PIX:` strip
frames plus optional `VOX:` speaker commands.

## Board

ESP32-S3-DevKitC-1 (N8R8). Seated on the breadboard across the center channel.
Connects to the Raspberry Pi 3 by USB serial at 115200 baud. The Pi runs the
collector and sends `LED:<brightness>` back over the same connection.

## Devices

- INMP441: I2S MEMS microphone, sound. (coded + enabled)
- BH1750: I2C ambient light, lux. (coded + enabled)
- BME280: I2C temperature / humidity / pressure. (coded + enabled)
- MPU-6050 / ICM-20689: I2C IMU, motion + orientation. (coded + enabled)
- MAX98357A + 4Ω 3W speaker: I2S audio output. (coded + enabled; `VOX:` optional)
- SK6812 RGBW strip: addressable emitter, ~16 px. (coded + enabled; `PIX:`)
- Onboard NeoPixel (RGB@IO38): status pixel. No external wiring.

## Pin assignments

| Function            | ESP32-S3 pin | Device pin | Notes                                  |
|---------------------|--------------|------------|----------------------------------------|
| I2S0 bit clock      | GPIO 5       | SCK        | INMP441 (coded)                        |
| I2S0 word select    | GPIO 6       | WS         | INMP441 (coded)                        |
| I2S0 data in        | GPIO 7       | SD         | INMP441 data out (coded)               |
| INMP441 channel sel | GND          | L/R        | L/R to GND = left channel. Do not float |
| I2C data            | GPIO 8       | SDA        | BH1750 + BME280 + IMU, shared          |
| I2C clock           | GPIO 9       | SCL        | BH1750 + BME280 + IMU, shared          |
| BH1750 address      | GND          | ADDR       | ADDR to GND = 0x23                     |
| BME280 address      | GND          | SDO        | SDO to GND = 0x76                      |
| IMU address         | GND          | AD0        | AD0 to GND = 0x68                      |
| I2S1 bit clock      | GPIO 15      | BCLK       | MAX98357A                              |
| I2S1 word select    | GPIO 16      | LRC        | MAX98357A                              |
| I2S1 data out       | GPIO 17      | DIN        | MAX98357A                              |
| SK6812 data         | GPIO 4       | DIN        | via 470Ω on perfboard                  |
| Onboard RGB LED     | GPIO 38      | -          | NeoPixel status pixel, no ext wiring   |
| 3V3                 | 3V3 rail     | VDD / VIN  | All sensors, mic, and amp              |
| 5V                  | 5V pin       | +5V        | SK6812 strip only                      |
| Ground              | GND rail     | GND        | Single common ground for everything    |

Pins avoided on the N8R8: 0/3/45/46 (strapping), 19/20 (USB), 43/44
(USB-serial), 26-37 (flash and octal PSRAM), 38 (onboard pixel).

## I2C bus

These devices share GPIO 8/9. No address clash:

- BH1750 = 0x23
- BME280 = 0x76
- MPU-6050 / ICM-20689 = 0x68
- MAX17048 fuel gauge = 0x36 (untethered power add-on, see below)

The GY-302 (BH1750) and GY-521 (IMU) breakouts carry onboard pull-ups, so no
external I2C resistors are needed.

## Unused pins — leave open (n/c)

These are intentionally not wired. Not omissions.

- IMU **INT** — motion interrupt. Firmware polls, so unused.
- IMU **XCL** and **XDA** — auxiliary I2C master bus for chaining a sensor onto
  the IMU. Not used here.
- MAX98357A **GAIN** and **SD** — left floating for default gain and enable.

Required power pins that are easy to miss next to the open ones: BH1750 **VCC**
and IMU **VCC** to 3V3, and IMU **AD0** to GND (sets 0x68).

## Audio

Two separate I2S peripherals so the mic and amp never share pins:

- I2S0 (RX) = INMP441 mic, on GPIO 5/6/7. Already in firmware.
- I2S1 (TX) = MAX98357A amp, on GPIO 15/16/17. Needs firmware.

MAX98357A: VIN to 3V3 (gives ~1.3W into 4Ω — enough for quiet use; 5V would give
the full 3W). Leave SD and GAIN floating for default enable and gain. Speaker +
and - to the screw terminal.

## SK6812 strip

- DIN to GPIO 4 through the 470Ω series resistor on the perfboard.
- +5V from the +5V rail (PowerBoost output), GND to the common rail.
- 1000µF cap across 5V/GND at the strip input (on the perfboard).
- ~16 px cut. Cap global brightness (~40% or less) and avoid all-channels-white.
  On the PowerBoost (~1A budget) that keeps the whole node in range. If you ever
  bench-power the strip from the 3V3 pin instead, keep brightness very low to
  protect the onboard regulator.
- Library: Adafruit NeoPixel, `NEO_GRBW + NEO_KHZ800`, colour order GRBW. This is
  a second NeoPixel object, separate from the onboard `NEO_GRB` status pixel.

## Power

Power comes from the PowerBoost 1000C 5V output. Right now it is fed by the
PowerBoost microUSB; the LiPo takes over later (see the PowerBoost add-on below).
That 5V is the system +5V rail. The ESP USB-C is only for programming.

- PowerBoost 5V to the +5V rail. PowerBoost GND to the common ground rail.
- ESP 5V IN pin from the +5V rail. The onboard regulator makes 3.3V from it.
- SK6812 +5V from the +5V rail. The strip is the only direct 5V load.
- Sensors, mic, and amp take 3V3 from the ESP 3V3 pin, GND from the common rail.
  That 3V3 is the regulator output, so the same source powers it.
- One common ground for everything.

Notes:

- The board's bottom-corner pin labelled 5V is an INPUT (5V IN). It does not output
  5V, so the strip is fed from the PowerBoost rail, not from the ESP.
- ESP32-S3 GPIO is 3.3V. Never feed 5V into a GPIO. The INMP441 is a 1.8 to 3.3V
  part, 3.3V only.
- The 5V IN pin is diode-protected, so the USB-C and the PowerBoost can be plugged
  in at the same time without back-feeding.

## PowerBoost 1000C, untethered power (add-on)

For the untethered build, a LiPo feeds the Adafruit PowerBoost 1000C. Its 5V boost
output becomes the system 5V rail. The strip and the ESP both run from it, and the
MAX17048 fuel gauge watches the battery.

Power path:

- LiPo (3.7V 2500mAh) into the PowerBoost JST. Check polarity before plugging in. A
  reversed cell damages the board. Keep this lead short.
- PowerBoost 5V pad to the +5V rail. PowerBoost GND pad to the common ground rail.
- SK6812 +5V to the +5V rail. The strip now gets true 5V, so full brightness and
  working whites.
- ESP 5V IN pin to the +5V rail. The board powers through its own input diode.
- One common ground for PowerBoost, ESP, strip, and sensors.
- Charge by plugging a microUSB into the PowerBoost's own port. It load-shares, so
  the node keeps running while charging.

3V3 from the LiPo (nothing extra to wire):

The sensors stay on the ESP 3V3 pin, which is the onboard regulator output. Feeding
PowerBoost 5V into 5V IN runs that regulator, so the 3V3 rail and every sensor on it
is powered from the battery automatically. Never put 5V on the 3V3 pin.

MAX17048 fuel gauge (I2C, address 0x36):

- VIN to battery positive. Tap the PowerBoost BAT pad, which is wired straight to the
  JST. VIN must see the raw cell (3.0 to 4.2V), not 3V3, or the reading is useless.
- GND to common ground.
- SDA to GPIO 8, SCL to GPIO 9. Shares the I2C bus, no address clash.
- Library when coding: Adafruit MAX1704X. Reports cell voltage and state of charge.

On/off and cautions:

- Optional power switch: any small switch between EN and GND. Open is on, shorted to
  GND is off. EN is a signal pin, so the switch carries no load current.
- Do not wire LBO to a GPIO. It is pulled to battery voltage (about 4.2V), above the
  3.3V limit. Use the MAX17048 for low-battery sensing instead.
- The PowerBoost 1000C gives about 1A continuous and sags past ~500mA. ESP plus WiFi
  plus 16 px at moderate brightness stays under that. Full-white on all pixels can
  exceed 1A and cause sag or cutout, so keep strip brightness capped.
- Program over the ESP USB-C. Run untethered on the PowerBoost. The board's input
  diodes let both be connected at once.

## Notes and gotchas

- INMP441 L/R must be tied (GND for left). Floating L/R gives unstable data;
  tied to VDD gives near-zero on the left slot the firmware reads.
- If `sound_rms` sits near zero: check SD on GPIO 7, that SCK/WS (5/6) are not
  swapped, and that L/R is grounded. Loose breadboard contact on the I2S clock
  lines also causes intermittent reads — solder the headers.
- If `light_lux` reads -1: check SDA/SCL (8/9) and 3V3/GND to the BH1750.
- The IMU reports `WHO_AM_I = 0x98`, so it behaves as an ICM-20689, not a strict
  MPU-6050. Use the `finani/ICM20689` library when adding it to firmware.
- Only one program can hold the ESP serial port. Do not run the PlatformIO
  Serial Monitor and the Pi collector at once.

## Firmware status

The firmware streams one JSON line per sample at ~10 Hz over USB serial and WiFi
TCP when configured. Raw values are normalized 0-1 by the Pi collector.

On boot, current firmware runs a strip proof on GPIO 4: red, green, blue, white,
then a dim blue idle fill. If the onboard ESP LEDs are alive but the strip stays
dark during that proof, check strip 5V, common ground, the 470 ohm data resistor,
DIN direction, and whether the ESP has actually been flashed with the current
firmware.

Runtime output protocol:

- `LED:<0-255>`: legacy status command. Drives the onboard RGB pixel and mirrors
  a capped blue fill to the SK6812 strip.
- `PIX:r,g,b,w,...`: full v06 RGBW strip frame, up to 16 pixels.
- `VOX:freq,ms[,vol]`: optional speaker tone. The live collector keeps this off
  unless `CREATURE_ENABLE_VOICE=1`.

## History

- v06: Added BME280, IMU, MAX98357A amp + speaker, and the SK6812 RGBW strip to
  the v05 base. Mic moved to its own I2S peripheral (I2S0); amp on I2S1.
- v05: ESP on breadboard. INMP441 (I2S) and BH1750 (I2C). Photoresistor retired.
- Earlier: ESP beside the breadboard, photoresistor divider on GPIO 4. Archived.


## Wire colours

Six colours, six net groups, one to one. White is reassigned from the old
"optional/expansion" use to 3V3, so 3V3 and 5V are never confused.

| Colour | Use | Wires |
|--------|-----|-------|
| Black  | GND | every ground (one common net) |
| Red    | 5V  | SK6812 +5V only |
| White  | 3V3 | power to BH1750, BME280, IMU, INMP441, MAX98357A |
| Yellow | I2C SDA | GPIO 8 → all three I2C sensors |
| Blue   | I2C SCL | GPIO 9 → all three I2C sensors |
| Green  | digital signal | I2S (mic 5/6/7, amp 15/16/17) + LED data (GPIO 4) |

Green carries seven signal lines, so green alone does not identify a net — keep
those runs short and read the schematic. The speaker is not in this scheme: its
two leads go from the MAX98357A screw terminal to the speaker, not to the ESP.
