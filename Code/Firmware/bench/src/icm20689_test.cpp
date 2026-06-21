// Creature v06 bench test: IMU motion sense (I2C, addr 0x68).
// Your part reports WHO_AM_I = 0x98, so it is an ICM-20689, not a true MPU-6050.
// This test uses raw I2C register reads, so it needs NO library and will compile
// regardless of which IMU library you later choose for the firmware. The register
// map (0x75 WHO_AM_I, 0x6B power, 0x3B accel) is shared by the MPU-6050 family.
//
// Pass: the single motion scalar rises when you move or tap the board, settles
// near zero when still. WHO_AM_I prints 0x98 (or 0x68 for a real MPU-6050).

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#define I2C_SDA          8
#define I2C_SCL          9
#define IMU_ADDR         0x68   // AD0 tied to GND
#define REG_WHOAMI       0x75
#define REG_PWR_MGMT_1   0x6B
#define REG_ACCEL_XOUT_H 0x3B

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 1);
  return Wire.available() ? Wire.read() : 0;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println();
  Serial.println("[imu] bench test  (addr 0x68, MPU-6050 / ICM-20689 family)");

  uint8_t who = readReg(REG_WHOAMI);
  Serial.print("WHO_AM_I = 0x");
  Serial.println(who, HEX);
  Serial.println("expected 0x98 (ICM-20689) or 0x68 (true MPU-6050)");

  // Wake the device (clear sleep bit).
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(REG_PWR_MGMT_1);
  Wire.write(0x00);
  Wire.endTransmission();
  delay(100);
}

void loop() {
  // Burst-read the 6 accelerometer bytes. Read into an array in order: the
  // order of evaluation inside one expression is not guaranteed in C++.
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 6);

  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.available() ? Wire.read() : 0;

  int16_t ax = (int16_t)((b[0] << 8) | b[1]);
  int16_t ay = (int16_t)((b[2] << 8) | b[3]);
  int16_t az = (int16_t)((b[4] << 8) | b[5]);

  // Default full-scale is +/-2g => 16384 LSB per g.
  float gx = ax / 16384.0f, gy = ay / 16384.0f, gz = az / 16384.0f;
  float mag = sqrtf(gx * gx + gy * gy + gz * gz);
  float motion = fabsf(mag - 1.0f);  // deviation from 1g at rest = the scalar

  Serial.print("a = [");
  Serial.print(gx, 2); Serial.print(", ");
  Serial.print(gy, 2); Serial.print(", ");
  Serial.print(gz, 2);
  Serial.print("] g   motion = ");
  Serial.println(motion, 3);
  delay(200);
}
