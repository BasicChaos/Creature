# KiCad Schematic Workflow

Version: May 30 2026
KiCad target: 10.x (works on 8 and 9 too)

This folder holds the documentation schematics for the Creature hardware. Goal:
a clean, readable schematic per node, exported to PDF, with source and PDF both
in git. No PCB layout for now.

## Files

- `Creature.kicad_pro` - the KiCad project. Open this one.
- `Creature.kicad_sch` - the schematic. Pre-filled title block plus a note on
  the canvas telling you what to place. Delete that note once the schematic is
  drawn.
- `Creature.pdf` - the exported schematic (you create this, step 5).

The schematic mirrors `Hardware/wiring.md`. If wiring changes, both should change.

## One-time setup

1. Open KiCad 10. File > Open Project, pick `Creature.kicad_pro`.
2. Double-click the schematic sheet to open Eeschema.
3. KiCad may ask to upgrade the file format. Say yes, then save (Ctrl+S). This
   rewrites the files in the current format. Commit that as your first change.

## 1. Place the parts

Press `A` (or the op-amp icon) to add a symbol. Search, pick, click to place.
For the body node you need:

| Ref | Symbol to search       | Library / notes                                  |
|-----|------------------------|--------------------------------------------------|
| U1  | `ESP32-S3-DevKitC`     | If not found, use `MCU_Module:ESP32-S3-DevKitC-1` or any generic module/header to represent the board |
| R1  | `R_Photo` or `LDR`     | Photoresistor. `R_Photo` lives in the `Device` lib |
| R2  | `R`                    | Plain resistor, value 10k                        |

Power symbols are separate. Press `P` (Place Power), then add:

- `+3V3`
- `GND`

Set values by hovering a part and pressing `V`: R2 = `10k`, R1 = `LDR`.

## 2. Wire the divider

Press `W` to start a wire, click point to point, press Esc to stop.

Build this:

```
+3V3 --- R1 (LDR) --- SENSE_NODE --- R2 (10k) --- GND
                          |
                          +--- U1 GPIO4
```

So: +3V3 to the top of R1. Bottom of R1 to the top of R2 (that joint is the
sense node). Bottom of R2 to GND. From the sense node, run a wire to U1's GPIO4
pin.

Note on the current build: this is a breadboard build, so every connection runs
from the board header by jumper. The schematic wires straight to the board's
`3v3` and `G` pins rather than using separate `+3V3` and `GND` power symbols.
Both styles are correct. Do not use the `5V` pin; the divider and ADC run at
3.3V.

## 3. Label the net

Click the sense node wire, press `L` (Place Label), type `LIGHT_SENSE`, place it
on the wire. Labels are how you name a signal and they read better than long
wires. The onboard RGB LED is internal to the ESP32-S3 (GPIO38), so it needs no
external parts. Add a text note saying so (press `T`).

## 4. Run ERC (catch mistakes)

Inspect > Electrical Rules Checker > Run. It flags unconnected pins and missing
power. For a documentation schematic, a few warnings are fine. The one to fix:
"input power pin not driven" usually means you forgot a `+3V3` or `GND` power
symbol, or a wire does not actually touch a pin. Green dots = connected.

## 5. Export the PDF

File > Plot. Set:

- Output format: `PDF`
- Output directory: this folder (`.`)
- Then `Plot`.

It writes `Creature.pdf` here. That is the readable snapshot that goes in git.

## 6. Commit

From the repo root:

```
git add Hardware/kicad/
git commit -m "hardware: body node schematic v A"
```

Commit the `.kicad_pro`, `.kicad_sch`, and `Creature.pdf`. Backup files
(`*-bak`, `_autosave-*`, `*-backups/`) are git-ignored already, so they will not
show up.

## Revisions

When the hardware changes, bump the revision so the PDF history stays clear.

1. Eeschema: File > Page Settings, change `Revision` (A, B, C ...).
2. Update the matching part of `Hardware/wiring.md`.
3. Re-export the PDF (step 5).
4. Commit with the new rev in the message.

## Adding more nodes later

Two clean options as the Creature grows:

- New sheet in the same project: Eeschema > Place > Hierarchical Sheet. Good for
  a second node that talks to the same system.
- New project folder: copy this folder pattern (e.g. `Hardware/kicad-sensor-x/`)
  for a fully separate board.

Keep one node per sheet so each PDF stays readable.
