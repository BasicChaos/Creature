// Creature v06 bench test: BME280 weather sense (I2C, addr 0x76).
// This is the sense that must stay nonzero at night, so confirm it is genuinely
// reading, not stuck.
//
// Pass: temperature reads a plausible room value, pressure a plausible absolute
// value (about 950-1040 hPa near sea level), both nonzero and stable.
//
// Library: adafruit/Adafruit BME280 Library (set in platformio.ini env:bme280).
// Note: some boards sold as "BME280" are actually BMP280 and report no humidity.
// If humidity reads 0 or NaN but temp/pressure are good, you have a BMP280.

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>

#define I2C_SDA  8
#define I2C_SCL  9
#define BME_ADDR 0x76   // SDO tied to GND. Try 0x77 if begin() fails.

Adafruit_BME280 bme;
bool ready = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println();
  Serial.println("[bme280] bench test  (addr 0x76)");
  ready = bme.begin(BME_ADDR, &Wire);
  if (!ready) {
    Serial.println("begin() FAILED - check addr (try 0x77), wiring, 3V3/GND.");
  }
}

void loop() {
  if (!ready) {
    ready = bme.begin(BME_ADDR, &Wire);
    delay(1000);
    return;
  }

  float t = bme.readTemperature();        // degrees C
  float p = bme.readPressure() / 100.0f;  // hPa
  float h = bme.readHumidity();           // %

  Serial.print("temp ");  Serial.print(t, 2);  Serial.print(" C   ");
  Serial.print("press "); Serial.print(p, 1);  Serial.print(" hPa   ");
  Serial.print("hum ");   Serial.print(h, 1);  Serial.println(" %");

  bool plausible = (t > 0 && t < 50 && p > 800 && p < 1100);
  Serial.println(plausible ? "PASS-ish: plausible and should hold steady."
                           : "CHECK: value implausible, look at wiring/part.");
  delay(1000);
}
