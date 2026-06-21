// Creature v06 bench test: I2C bus scan.
// Run this FIRST. Three of the four senses sit on I2C. If a device does not
// show here, it is wiring or power, not code. Fix it before testing that part.
//
// Pins match WIRING v06: SDA=8, SCL=9.
// Expected addresses: 0x23 BH1750 (light), 0x68 IMU, 0x76 BME280 (weather).
// The INMP441 mic and MAX98357A amp are I2S, not I2C, so they never appear here.

#include <Arduino.h>
#include <Wire.h>

#define I2C_SDA 8
#define I2C_SCL 9

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println();
  Serial.println("[i2c_scan] bench test  (SDA=8, SCL=9)");
  Serial.println("expected: 0x23 BH1750, 0x68 IMU, 0x76 BME280");
}

void loop() {
  int found = 0;
  Serial.println("scanning...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("  found 0x");
      Serial.println(addr, HEX);
      found++;
    }
  }
  Serial.print("done: ");
  Serial.print(found);
  Serial.println(" device(s)");
  if (found == 0) {
    Serial.println("NONE found - check 3V3, GND, SDA/SCL, and pull-ups.");
  } else {
    Serial.println("PASS if all 3 expected addresses are present.");
  }
  Serial.println();
  delay(3000);
}
