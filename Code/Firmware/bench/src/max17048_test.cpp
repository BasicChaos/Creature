// Creature v06 bench test: MAX17048 LiPo fuel gauge (I2C, addr 0x36).
// This is the "power level" sense: cell voltage and state-of-charge over I2C, so
// the Creature knows its own energy instead of guessing from raw voltage.
//
// IMPORTANT: the MAX17048 is powered FROM the cell it measures. Its VIN must go to
// the raw battery (tap the PowerBoost BAT pad), NOT to 3V3. With no battery on VIN
// the chip has no power and will not appear on I2C at all, so run this only after
// the LiPo is wired.
//
// Pass: begin() succeeds; cell voltage reads a plausible LiPo value (3.0-4.2V) and
// percent 0-100, both stable. Pull the charge cable and the rate should go negative
// (discharging); plug it in and the rate should go positive.
//
// Library: adafruit/Adafruit MAX1704X (set in platformio.ini env:fuelgauge).

#include <Arduino.h>
#include <Wire.h>
#include "Adafruit_MAX1704X.h"

#define I2C_SDA 8
#define I2C_SCL 9

Adafruit_MAX17048 maxlipo;
bool ready = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println();
  Serial.println("[max17048] bench test  (addr 0x36)");
  ready = maxlipo.begin(&Wire);
  if (!ready) {
    Serial.println("  not found. Is the LiPo on VIN? The gauge is powered by the cell.");
  } else {
    Serial.println("  found. reading cell voltage and charge...");
  }
}

void loop() {
  if (!ready) {
    ready = maxlipo.begin(&Wire);   // keep retrying until the battery is on VIN
    delay(1000);
    return;
  }
  float volts = maxlipo.cellVoltage();
  float pct   = maxlipo.cellPercent();
  float rate  = maxlipo.chargeRate();   // %/hr: positive charging, negative discharging

  Serial.print("cell=");    Serial.print(volts, 3);
  Serial.print("V  charge="); Serial.print(pct, 1);
  Serial.print("%  rate=");   Serial.print(rate, 1);
  Serial.println("%/hr");
  delay(1000);
}
