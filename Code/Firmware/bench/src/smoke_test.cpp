// Creature v06 bench test: COMBINED smoke test.
// One flash that brings up every part at once, now that the whole node is wired.
// This is the "does it all come alive" check. For isolating a single failing
// part, use the per-part envs (i2c_scan, bme280, imu, sk6812, speaker) instead.
//
// What it does, every cycle:
//   - reads BH1750 light, BME280 weather, ICM-20689 motion (all I2C, SDA8/SCL9)
//   - reads the INMP441 mic (I2S0) and reports a sound level
//   - animates the SK6812 strip (GPIO 4) at low brightness
//   - blinks the onboard status pixel as a heartbeat
//   - every few seconds, plays a short quiet tone on the MAX98357A (I2S1)
// It prints one human-readable status line per cycle with a PASS/-- per part.
//
// Pins are from WIRING v06. The mic uses I2S peripheral 0, the amp uses I2S
// peripheral 1, so the two never share pins. Platform is pinned to Arduino-
// ESP32 2.x (espressif32 6.x), so this uses the legacy driver/i2s.h API.

#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include <Adafruit_NeoPixel.h>
#include <BH1750.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <driver/i2s.h>

// ---- pins (WIRING v06) -----------------------------------------------------
#define I2C_SDA 8
#define I2C_SCL 9

#define MIC_PORT I2S_NUM_0
#define MIC_SCK 5
#define MIC_WS 6
#define MIC_SD 7

#define AMP_PORT I2S_NUM_1
#define AMP_BCLK 15
#define AMP_LRC 16
#define AMP_DIN 17

#define STRIP_PIN 4
#define NUM_PX 16
// New N16R8 boards usually put the onboard RGB on GPIO 48. If it stays dark,
// change this to 38 (older DevKitC-1). It does not affect anything else.
#define ONBOARD_RGB_PIN 48

// ---- I2C addresses ---------------------------------------------------------
#define BME_ADDR 0x76 // SDO to GND; try 0x77 if begin() fails
#define IMU_ADDR 0x68 // AD0 to GND
#define IMU_WHOAMI 0x75
#define IMU_PWR 0x6B
#define IMU_ACCEL 0x3B

// ---- audio -----------------------------------------------------------------
#define SAMPLE_RATE 16000
#define MIC_SAMPLES 256
#define TONE_AMP 0.25f // 0..1 of full scale, same level as the clean bench test

Adafruit_NeoPixel strip(NUM_PX, STRIP_PIN, NEO_GRBW + NEO_KHZ800);
Adafruit_NeoPixel onboard(1, ONBOARD_RGB_PIN, NEO_GRB + NEO_KHZ800);
BH1750 lightMeter;
Adafruit_BME280 bme;

bool lightOK = false, bmeOK = false, imuOK = false;
uint8_t imuWho = 0;
int32_t micBuf[MIC_SAMPLES];
unsigned long lastCycle = 0, lastTone = 0, lastBeat = 0;
int comet = 0;
float tonePhase = 0.0f; // continuous phase across tones, exactly like the bench test

// ---- IMU raw register helpers (no library needed) --------------------------
uint8_t imuReadReg(uint8_t reg)
{
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 1);
  return Wire.available() ? Wire.read() : 0;
}

// Returns a motion scalar: how far total acceleration is from 1g (still ~= 0).
float imuMotion()
{
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(IMU_ACCEL);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 6);
  if (Wire.available() < 6)
    return -1.0f;
  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();
  float gx = ax / 16384.0f, gy = ay / 16384.0f, gz = az / 16384.0f; // +-2g
  float mag = sqrtf(gx * gx + gy * gy + gz * gz);
  return fabsf(mag - 1.0f);
}

// ---- INMP441 mic on I2S0 (RX) ----------------------------------------------
void micSetup()
{
  i2s_config_t cfg = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 4,
      .dma_buf_len = MIC_SAMPLES,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0};
  i2s_pin_config_t pins = {
      .mck_io_num = I2S_PIN_NO_CHANGE,
      .bck_io_num = MIC_SCK,
      .ws_io_num = MIC_WS,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = MIC_SD};
  i2s_driver_install(MIC_PORT, &cfg, 0, NULL);
  i2s_set_pin(MIC_PORT, &pins);
}

// RMS of one short read, scaled down so the printed number is easy to read.
float micRMS()
{
  size_t bytesRead = 0;
  i2s_read(MIC_PORT, micBuf, sizeof(micBuf), &bytesRead, pdMS_TO_TICKS(50));
  int n = bytesRead / sizeof(int32_t);
  if (n <= 0)
    return 0.0f;
  double sumSq = 0;
  for (int i = 0; i < n; i++)
  {
    int32_t s = micBuf[i] >> 8; // 24-bit sample in the top bits
    sumSq += (double)s * (double)s;
  }
  return (float)(sqrt(sumSq / n) / 1000.0);
}

// ---- MAX98357A amp on I2S1 (TX) --------------------------------------------
void ampSetup()
{
  i2s_config_t cfg = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
      .sample_rate = SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false, // match the working standalone speaker test exactly
      .tx_desc_auto_clear = true,
      .fixed_mclk = 0};
  i2s_pin_config_t pins = {
      .mck_io_num = I2S_PIN_NO_CHANGE,
      .bck_io_num = AMP_BCLK,
      .ws_io_num = AMP_LRC,
      .data_out_num = AMP_DIN,
      .data_in_num = I2S_PIN_NO_CHANGE};
  i2s_driver_install(AMP_PORT, &cfg, 0, NULL);
  i2s_set_pin(AMP_PORT, &pins);
  i2s_zero_dma_buffer(AMP_PORT);
}

// Write `ms` of silence to the amp. Used to settle the clock after driver churn
// and to drain the tone's tail so it is not chopped off (chopping = end click).
void ampSilence(int ms)
{
  int16_t z[256] = {0};
  int total = (SAMPLE_RATE * ms) / 1000;
  int done = 0;
  while (done < total)
  {
    int n = min(256, total - done);
    size_t written = 0;
    i2s_write(AMP_PORT, z, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
  }
}

void playTone(float freq, int ms)
{
  i2s_driver_uninstall(MIC_PORT); // during the tone only the amp runs
  ampSilence(40);                 // settle the clock after the churn and warm the amp (fixes first-tone distortion)

  const float dt = 2.0f * (float)M_PI * freq / SAMPLE_RATE;
  const int total = (SAMPLE_RATE * ms) / 1000;
  const int fade = SAMPLE_RATE / 100; // ~10 ms fade in/out kills the start/finish clicks
  int16_t buf[256];
  int done = 0;
  while (done < total)
  {
    int n = min(256, total - done);
    for (int i = 0; i < n; i++)
    {
      int idx = done + i;
      float env = 1.0f;
      if (idx < fade)
        env = (float)idx / fade; // fade in
      else if (idx > total - fade)
        env = (float)(total - idx) / fade; // fade out
      buf[i] = (int16_t)(TONE_AMP * 32767.0f * env * sinf(tonePhase));
      tonePhase += dt;
      if (tonePhase > 2.0f * (float)M_PI)
        tonePhase -= 2.0f * (float)M_PI;
    }
    size_t written = 0;
    i2s_write(AMP_PORT, buf, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
  }
  ampSilence(150); // drain the faded tail fully before touching the mic (no abrupt cut)
  micSetup();      // reinstall the mic for the next listening cycle
}

// ---- strip: one-time RGBW proof, then a low comet ---------------------------
void stripProof()
{
  // Full channel values; the setBrightness(40) cap is the single current limiter,
  // so these are clearly visible without risking a brownout (one channel at a time).
  uint32_t cols[4] = {
      strip.Color(255, 0, 0, 0), strip.Color(0, 255, 0, 0),
      strip.Color(0, 0, 255, 0), strip.Color(0, 0, 0, 255)};
  for (int c = 0; c < 4; c++)
  {
    for (int i = 0; i < NUM_PX; i++)
      strip.setPixelColor(i, cols[c]);
    strip.show();
    delay(350);
  }
  strip.clear();
  strip.show();
}

void stripComet()
{
  strip.clear();
  uint16_t hue = (uint16_t)((comet * 4000) % 65536);
  // bright head + fading 2-pixel tail; brightness(40) still caps total current
  strip.setPixelColor(comet % NUM_PX, strip.ColorHSV(hue, 255, 255));
  strip.setPixelColor((comet + NUM_PX - 1) % NUM_PX, strip.ColorHSV(hue, 255, 90));
  strip.setPixelColor((comet + NUM_PX - 2) % NUM_PX, strip.ColorHSV(hue, 255, 30));
  strip.show();
  comet++;
}

void setup()
{
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("[smoke] Creature v06 combined bring-up");

  Wire.begin(I2C_SDA, I2C_SCL);

  strip.begin();
  strip.setBrightness(40); // keep low on USB 5V; never all-white
  strip.clear();
  strip.show();
  onboard.begin();
  onboard.setBrightness(40);
  onboard.clear();
  onboard.show();

  lightOK = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);
  bmeOK = bme.begin(BME_ADDR, &Wire);

  imuWho = imuReadReg(IMU_WHOAMI);
  imuOK = (imuWho != 0x00 && imuWho != 0xFF);
  if (imuOK)
  { // wake from sleep
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(IMU_PWR);
    Wire.write(0x00);
    Wire.endTransmission();
  }

  micSetup();
  ampSetup();

  Serial.print("  light(BH1750): ");
  Serial.println(lightOK ? "PASS" : "-- not found");
  Serial.print("  weather(BME280):");
  Serial.println(bmeOK ? "PASS" : "-- not found (try 0x77)");
  Serial.print("  motion(IMU):    ");
  Serial.print(imuOK ? "PASS  WHO_AM_I=0x" : "-- WHO_AM_I=0x");
  Serial.println(imuWho, HEX);
  Serial.println("  running RGBW strip proof...");
  stripProof();
  playTone(523.0f, 120); // short soft chirp = boot done
  Serial.println("  status line follows. tap the board, cover the light, make noise.");
}

void loop()
{
  unsigned long now = millis();

  // heartbeat on the onboard pixel, gated so show() does not spin every loop
  if (now - lastBeat >= 250)
  {
    lastBeat = now;
    onboard.setPixelColor(0, (now / 500) % 2 ? onboard.Color(0, 20, 0) : 0);
    onboard.show();
  }

  if (now - lastCycle >= 600)
  {
    lastCycle = now;

    float lux = lightOK ? lightMeter.readLightLevel() : -1.0f;
    float tempC = bmeOK ? bme.readTemperature() : NAN;
    float hPa = bmeOK ? bme.readPressure() / 100.0f : NAN;
    float motion = imuOK ? imuMotion() : -1.0f;
    float rms = micRMS();

    Serial.print("light=");
    Serial.print(lux, 1);
    Serial.print("lx  temp=");
    Serial.print(tempC, 1);
    Serial.print("C  press=");
    Serial.print(hPa, 1);
    Serial.print("hPa  motion=");
    Serial.print(motion, 3);
    Serial.print("  mic_rms=");
    Serial.println(rms, 1);

    stripComet();
  }

  // longer test tone so audio quality is easy to judge (nothing else runs during it)
  if (now - lastTone >= 3000)
  {
    lastTone = now;
    playTone(192.999f, 2000);
  }
}
