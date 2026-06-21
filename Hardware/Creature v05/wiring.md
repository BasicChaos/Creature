# Creature Hardware Wiring

Version: June 6 2026

Current body node. Reflects the live firmware in
`Code/Firmware/esp-creature-core/src/main.cpp` (v.05).

## Board

ESP32-S3-DevKitC-1 (N8R8). Seated directly on the breadboard, straddling the
center channel. Connects to the Raspberry Pi 3 by USB serial at 115200 baud. The
Pi runs the collector and sends `LED:<brightness>` commands back over the same
connection.

## Sensors

- INMP441: I2S MEMS microphone, sound. Cell 1 anchor.
- BH1750: I2C ambient light sensor, lux. Cell 4 anchor.
- Onboard NeoPixel (RGB@IO38): emitter. Cell 8. No external wiring.

The photoresistor divider is retired in v.05. Parts kept for later.

## Pin assignments

| Function             | ESP32-S3 pin | Sensor pin | Notes                                |
|----------------------|--------------|------------|--------------------------------------|
| I2S bit clock        | GPIO 5       | SCK        | INMP441                              |
| I2S word select      | GPIO 6       | WS         | INMP441                              |
| I2S data in          | GPIO 7       | SD         | INMP441 data out                     |
| INMP441 channel sel  | GND          | L/R        | L/R to GND selects the left channel  |
| I2C data             | GPIO 8       | SDA        | BH1750                               |
| I2C clock            | GPIO 9       | SCL        | BH1750                               |
| BH1750 address       | GND          | ADDR       | ADDR to GND gives address 0x23       |
| Onboard RGB LED      | GPIO 38      | -          | NeoPixel, no external wiring         |
| 3V3                  | 3V3 rail     | VDD / VCC  | Both sensors power from the 3V3 rail |
| Ground               | GND rail     | GND        | Shared ground on the GND rail        |

Pins avoided on the N8R8: 0/3/45/46 (strapping), 19/20 (USB), 43/44
(USB-serial), 26-37 (flash and octal PSRAM).

## Physical build

The ESP32-S3 sits on the breadboard, straddling the center channel at one end,
USB facing off the end so the Pi cable does not lever the board. Both sensor
breakouts sit further along the same board, also across the center channel.

Power is shared through the side rails:

- ESP 3V3 to the red (+) rail, ESP GND to the blue (-) rail.
- Each sensor takes VDD/VCC from the red rail and GND from the blue rail.
- Signal pins run as short jumpers from the ESP GPIOs to each sensor.

The BH1750 (GY-302) has onboard I2C pull-ups, so no external resistors are
needed. If the DevKitC-1 leaves no free holes on the module side, join a second
breadboard along the long edge, remove the two facing power rails, and seat the
ESP so each pin row lands on its own board.

See `wiring-diagram.svg` for the visual version and `kicad/Creature.pdf` for the
formal schematic (source in `kicad/Creature.kicad_sch`). Both still show the old
photoresistor build and need updating to this pinout.

## Firmware output

One JSON line per sample at about 10 Hz over USB serial:

```text
{"time_ms":123456,"light_lux":214.2,"sound_rms":2371.0}
```

Values are raw. The Pi collector normalizes `light_lux` and `sound_rms` to 0-1.
`light_lux` is -1 if the BH1750 did not initialize. The startup line reports
`light_ready`.

## Parts list

- ESP32-S3-DevKitC-1 (N8R8) dev board
- INMP441 I2S microphone breakout
- BH1750 (GY-302) light sensor breakout
- Breadboard and jumper wires
- USB cable (ESP to Raspberry Pi 3)

No external LED or resistors needed. The onboard RGB LED is the only emitter.

## Behavior reference

- Soft blue blink on LED command.
- Brightness tracks output from the Pi. Pulse speed tracks pressure.

## Notes and gotchas

- ESP32-S3 GPIO is 3.3V. Do not feed 5V into any pin.
- Only one program can hold the ESP serial port at a time. Do not run the
  PlatformIO Serial Monitor and the collector at once.
- INMP441 L/R must be tied (GND for left). Floating L/R gives unstable data.
- If `sound_rms` sits near zero, check SD on GPIO 7 and that L/R is grounded.
- If `light_lux` reads -1, check SDA/SCL (8/9) and 3V3/GND to the BH1750.

## History

- v.05: ESP on breadboard. INMP441 (I2S) and BH1750 (I2C). Photoresistor retired.
- Earlier: ESP beside the breadboard, photoresistor divider on GPIO 4. Archived
  under `Archive/`.
