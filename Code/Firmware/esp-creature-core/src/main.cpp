#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include <BH1750.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <driver/i2s.h>
#include <math.h>

#if __has_include("creature_wifi_secrets.h")
#include "creature_wifi_secrets.h"
#endif

// ---------------------------------------------------------------------------
// Creature body node firmware (v06)
// Board: ESP32-S3-DevKitC-1-N8R8
//
// Bring-up switches: enable one sensor at a time while the wiring is settled.
//   ENABLE_MIC    1 = read the INMP441 (I2S), stream sound_rms
//   ENABLE_LIGHT  1 = read the BH1750 (I2C), stream light_lux
// A sensor set to 0 is skipped completely: no init, no errors, not in output.
// Turn ENABLE_LIGHT back to 1 once the BH1750 header is soldered.
//
// Streams one JSON line per sample over USB serial and, when WiFi is configured,
// over a small TCP server, for example:
//   {"time_ms":<ms>,"sound_rms":<float>}
// The Raspberry Pi collector normalizes raw values to 0-1.
//
// A temporary "status" line prints every 2 seconds for bring-up debugging.
// Accepts "LED:<0-255>\n" for the onboard NeoPixel over USB or TCP, mirrored
// to the SK6812 strip as a v06 fallback. The full strip output is PIX:.
//
// WiFi is disabled unless CREATURE_WIFI_SSID is defined and non-empty. Keep real
// credentials out of git by creating include/creature_wifi_secrets.h with:
//   #define CREATURE_WIFI_SSID "your-network"
//   #define CREATURE_WIFI_PASSWORD "your-password"
// ---------------------------------------------------------------------------

#define ENABLE_MIC    1   // both sensors on (friction-pin contact until the iron arrives)
#define ENABLE_LIGHT  1
#define ENABLE_MOTION   1   // ICM-20689 IMU (I2C 0x68), stream "motion" scalar
#define ENABLE_WEATHER  1   // BME280 (I2C 0x76), stream "temp_c" and "pressure_hpa"
#define ENABLE_STRIP    1   // SK6812 RGBW strip (GPIO 4, 16 px), PIX: command
#define ENABLE_VOICE    1   // MAX98357A amp (I2S1 15/16/17), VOX: command
#define ENABLE_BOOT_CHIRP 0  // keep startup quiet; collector sends gentle VOX tones

// Most ESP32-S3 dev boards have a USB-serial activity LED that cannot be
// controlled as a GPIO. If a WiFi collector is connected, stop mirroring the
// 10 Hz JSON stream to USB Serial so that board LED does not blink constantly.
#ifndef QUIET_SERIAL_WHEN_WIFI_CLIENT
#define QUIET_SERIAL_WHEN_WIFI_CLIENT 1
#endif

#ifndef CREATURE_WIFI_SSID
#define CREATURE_WIFI_SSID ""
#endif

#ifndef CREATURE_WIFI_PASSWORD
#define CREATURE_WIFI_PASSWORD ""
#endif

#ifndef CREATURE_WIFI_HOSTNAME
#define CREATURE_WIFI_HOSTNAME "creature-esp"
#endif

#ifndef CREATURE_WIFI_PORT
#define CREATURE_WIFI_PORT 7777
#endif

// ---- Onboard RGB LED (emitter) --------------------------------------------
#define RGB_PIN     38
#define NUM_PIXELS  1
Adafruit_NeoPixel pixel(NUM_PIXELS, RGB_PIN, NEO_GRB + NEO_KHZ800);

// ---- BH1750 ambient light sensor (I2C) ------------------------------------
#define I2C_SDA_PIN 8
#define I2C_SCL_PIN 9
BH1750  lightMeter;
bool    lightReady = false;
uint8_t lightAddr  = 0x00;
int     i2cFoundCount = 0;

// ---- ICM-20689 / MPU-6050-family IMU (I2C 0x68) ---------------------------
#define IMU_ADDR          0x68   // AD0 to GND
#define IMU_REG_WHOAMI    0x75
#define IMU_REG_PWR_MGMT1 0x6B
#define IMU_REG_ACCEL     0x3B   // ACCEL_XOUT_H, 6 bytes follow
bool  imuReady = false;
float lastMotion = 0.0f;

// ---- BME280 weather sensor (I2C 0x76) -------------------------------------
#define BME_ADDR 0x76            // SDO to GND; setup falls back to 0x77
Adafruit_BME280 bme;
bool  bmeReady = false;
float lastTempC = 0.0f;
float lastPressureHpa = 0.0f;

// ---- SK6812 RGBW strip emitter (GPIO 4, 16 px) ----------------------------
#define STRIP_PIN            4
#define STRIP_COUNT          16
#define STRIP_MAX_BRIGHTNESS 40   // current cap on USB/PowerBoost; never all-white
Adafruit_NeoPixel strip(STRIP_COUNT, STRIP_PIN, NEO_GRBW + NEO_KHZ800);

// ---- MAX98357A amp emitter (I2S1, GPIO 15/16/17) --------------------------
#define AMP_PORT        I2S_NUM_1
#define AMP_BCLK_PIN    15
#define AMP_LRC_PIN     16
#define AMP_DIN_PIN     17
#define AMP_SAMPLE_RATE 16000
#define TONE_AMP        0.25f     // digital level; for quieter, lower the GAIN pin, not this
float tonePhase = 0.0f;           // continuous phase across tones (no click)

// ---- INMP441 microphone (I2S) ---------------------------------------------
#define I2S_PORT          I2S_NUM_0
#define I2S_SCK_PIN       5      // bit clock   (SCK / BCLK)
#define I2S_WS_PIN        6      // word select (WS / LRCL)
#define I2S_SD_PIN        7      // serial data out from the mic (SD)
#define I2S_SAMPLE_RATE   16000
#define I2S_SAMPLE_COUNT  256
int32_t i2sSamples[I2S_SAMPLE_COUNT];
float   lastSoundRms = 0.0f;
long    micCount = 0;
int32_t micMin = 0;
int32_t micMax = 0;
int32_t micFirst[8];           // first raw samples of the last read, for debug
int     micFirstN = 0;

// ---- Sampling --------------------------------------------------------------
const unsigned long SAMPLE_INTERVAL_MS = 100;   // 10 Hz output
unsigned long lastSampleMs = 0;

const unsigned long STATUS_INTERVAL_MS = 2000;  // temporary diagnostics
unsigned long lastStatusMs = 0;

// ---- Optional WiFi TCP bridge ---------------------------------------------
WiFiServer wifiServer(CREATURE_WIFI_PORT);
WiFiClient wifiClient;
bool wifiEnabled = strlen(CREATURE_WIFI_SSID) > 0;
bool wifiServerStarted = false;
bool mdnsStarted = false;
unsigned long lastWifiAttemptMs = 0;
const unsigned long WIFI_RETRY_INTERVAL_MS = 10000;

String serialCommand = "";
String wifiCommand = "";

// Forward declarations
void  setRgb(uint8_t red, uint8_t green, uint8_t blue);
void  scanI2C();
void  setupLight();
void  setupIMU();
float readMotion();
void  setupBME();
void  readWeather(float& tempC, float& pressureHpa);
void  setupStrip();
void  stripProof();
void  stripBootIdle();
void  applyLegacyStripBrightness(uint8_t brightness);
void  applyPixels(const String& csv);
void  ampSetup();
void  ampSilence(int ms);
void  playTone(float freq, int ms, float vol);
void  setupI2SMic();
float readSoundRms();
void  setupWifi();
void  serviceWifi();
void  readSerialCommands();
void  readWifiCommands();
bool  shouldWriteSerial();
void  writeLineToTransports(const String& line);
void  writeSystemLineToTransports(const String& line);

// ---------------------------------------------------------------------------
void handleCommand(String command, const char* source)
{
  command.trim();

  if (command.startsWith("LED:"))
  {
    int brightness = command.substring(4).toInt();
    brightness = constrain(brightness, 0, 255);

    pixel.setBrightness(brightness);
    setRgb(0, 0, 255);

#if ENABLE_STRIP
    applyLegacyStripBrightness((uint8_t)brightness);
#endif

    String response = "{\"system\":\"led_command_received\",\"source\":\"";
    response += source;
    response += "\",\"brightness\":";
    response += brightness;
#if ENABLE_STRIP
    response += ",\"strip_mirror\":true";
#endif
    response += "}";
    writeSystemLineToTransports(response);
  }
#if ENABLE_STRIP
  else if (command.startsWith("PIX:"))
  {
    applyPixels(command.substring(4));
  }
#endif
#if ENABLE_VOICE
  else if (command.startsWith("VOX:"))
  {
    // VOX:freq,ms[,vol]   freq in Hz, ms duration, vol 0-1
    String args = command.substring(4);
    int c1 = args.indexOf(',');
    int c2 = (c1 >= 0) ? args.indexOf(',', c1 + 1) : -1;
    float freq = (c1 >= 0 ? args.substring(0, c1) : args).toFloat();
    int   ms   = (c1 >= 0) ? args.substring(c1 + 1, (c2 >= 0) ? c2 : args.length()).toInt() : 150;
    float vol  = (c2 >= 0) ? args.substring(c2 + 1).toFloat() : 1.0f;
    if (freq > 0.0f && ms > 0) playTone(freq, ms, vol);
  }
#endif
}

void readSerialCommands()
{
  while (Serial.available() > 0)
  {
    char incomingChar = Serial.read();

    if (incomingChar == '\n')
    {
      handleCommand(serialCommand, "serial");
      serialCommand = "";
    }
    else
    {
      serialCommand += incomingChar;
    }
  }
}

void readWifiCommands()
{
  if (!wifiClient || !wifiClient.connected())
  {
    return;
  }

  while (wifiClient.available() > 0)
  {
    char incomingChar = wifiClient.read();

    if (incomingChar == '\n')
    {
      handleCommand(wifiCommand, "wifi");
      wifiCommand = "";
    }
    else
    {
      wifiCommand += incomingChar;
    }
  }
}

void writeLineToTransports(const String& line)
{
  if (shouldWriteSerial())
  {
    Serial.println(line);
  }

  if (wifiClient && wifiClient.connected())
  {
    wifiClient.println(line);
  }
}

void writeSystemLineToTransports(const String& line)
{
  writeLineToTransports(line);
}

bool shouldWriteSerial()
{
#if QUIET_SERIAL_WHEN_WIFI_CLIENT
  return !(wifiEnabled && wifiClient && wifiClient.connected());
#else
  return true;
#endif
}

void setupWifi()
{
  if (!wifiEnabled)
  {
    Serial.println("{\"system\":\"wifi_disabled\",\"reason\":\"no_ssid\"}");
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(CREATURE_WIFI_HOSTNAME);
  WiFi.begin(CREATURE_WIFI_SSID, CREATURE_WIFI_PASSWORD);
  lastWifiAttemptMs = millis();

  Serial.print("{\"system\":\"wifi_connecting\",\"ssid\":\"");
  Serial.print(CREATURE_WIFI_SSID);
  Serial.println("\"}");
}

void serviceWifi()
{
  if (!wifiEnabled)
  {
    return;
  }

  unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED)
  {
    if (now - lastWifiAttemptMs >= WIFI_RETRY_INTERVAL_MS)
    {
      lastWifiAttemptMs = now;
      wifiServerStarted = false;
      mdnsStarted = false;
      WiFi.disconnect();
      WiFi.begin(CREATURE_WIFI_SSID, CREATURE_WIFI_PASSWORD);
      Serial.println("{\"system\":\"wifi_reconnecting\"}");
    }
    return;
  }

  if (!wifiServerStarted)
  {
    wifiServer.begin();
    wifiServer.setNoDelay(true);
    wifiServerStarted = true;

    mdnsStarted = MDNS.begin(CREATURE_WIFI_HOSTNAME);
    if (mdnsStarted)
    {
      MDNS.addService("creature", "tcp", CREATURE_WIFI_PORT);
    }

    String line = "{\"system\":\"wifi_ready\",\"ip\":\"";
    line += WiFi.localIP().toString();
    line += "\",\"hostname\":\"";
    line += CREATURE_WIFI_HOSTNAME;
    line += ".local";
    line += "\",\"port\":";
    line += CREATURE_WIFI_PORT;
    line += ",\"mdns\":";
    line += mdnsStarted ? "true" : "false";
    line += "}";
    writeSystemLineToTransports(line);
  }

  if (!wifiClient || !wifiClient.connected())
  {
    WiFiClient newClient = wifiServer.available();
    if (newClient)
    {
      if (wifiClient)
      {
        wifiClient.stop();
      }
      wifiClient = newClient;
      wifiClient.setNoDelay(true);
      writeSystemLineToTransports("{\"system\":\"wifi_client_connected\"}");
    }
  }
}

void setRgb(uint8_t red, uint8_t green, uint8_t blue)
{
  pixel.setPixelColor(0, pixel.Color(red, green, blue));
  pixel.show();
}

// Print every I2C address that answers, and record how many were found.
void scanI2C()
{
  i2cFoundCount = 0;
  String line = "{\"system\":\"i2c_scan\",\"found\":[";
  bool first = true;
  for (uint8_t addr = 1; addr < 127; addr++)
  {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0)
    {
      if (!first) line += ",";
      line += "\"0x";
      line += String(addr, HEX);
      line += "\"";
      first = false;
      i2cFoundCount++;
    }
  }
  line += "]}";
  writeSystemLineToTransports(line);
}

// Try the BH1750 at both possible addresses: 0x23 (ADDR low) and 0x5C (high).
void setupLight()
{
  if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire))
  {
    lightReady = true;
    lightAddr  = 0x23;
  }
  else if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x5C, &Wire))
  {
    lightReady = true;
    lightAddr  = 0x5C;
  }
  else
  {
    lightReady = false;
    lightAddr  = 0x00;
  }
}

// Read one IMU register over I2C (no library needed).
uint8_t imuReadReg(uint8_t reg)
{
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 1);
  return Wire.available() ? Wire.read() : 0;
}

// Wake the IMU and confirm it answers. WHO_AM_I is 0x98 (ICM-20689) or 0x68.
void setupIMU()
{
  uint8_t who = imuReadReg(IMU_REG_WHOAMI);
  imuReady = (who == 0x98 || who == 0x68 || who == 0x70 || who == 0x71);
  if (imuReady)
  {
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(IMU_REG_PWR_MGMT1);
    Wire.write(0x00);              // clear the sleep bit
    Wire.endTransmission();
    delay(10);
  }
}

// Burst-read accel and return the deviation of |a| from 1g: about zero at rest,
// rising on movement or a tap. Default full scale is +/-2g => 16384 LSB per g.
float readMotion()
{
  if (!imuReady) return 0.0f;
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(IMU_REG_ACCEL);
  Wire.endTransmission(false);
  Wire.requestFrom((int)IMU_ADDR, 6);
  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.available() ? Wire.read() : 0;
  int16_t ax = (int16_t)((b[0] << 8) | b[1]);
  int16_t ay = (int16_t)((b[2] << 8) | b[3]);
  int16_t az = (int16_t)((b[4] << 8) | b[5]);
  float gx = ax / 16384.0f, gy = ay / 16384.0f, gz = az / 16384.0f;
  float mag = sqrtf(gx * gx + gy * gy + gz * gz);
  lastMotion = fabsf(mag - 1.0f);
  return lastMotion;
}

// Try the BME280 at 0x76, then 0x77.
void setupBME()
{
  bmeReady = bme.begin(BME_ADDR, &Wire) || bme.begin(0x77, &Wire);
}

// Read temperature (C) and pressure (hPa). Zero when the sensor is absent.
void readWeather(float& tempC, float& pressureHpa)
{
  if (!bmeReady)
  {
    tempC = 0.0f;
    pressureHpa = 0.0f;
    return;
  }
  tempC = bme.readTemperature();
  pressureHpa = bme.readPressure() / 100.0f;
  lastTempC = tempC;
  lastPressureHpa = pressureHpa;
}

// ---- SK6812 RGBW strip emitter --------------------------------------------
void setupStrip()
{
  strip.begin();
  strip.setBrightness(STRIP_MAX_BRIGHTNESS);   // current cap; never all-white
  strip.clear();
  strip.show();
}

// Boot proof: drive each colour channel across all pixels, one channel at a
// time. The brightness cap is the current limiter, so this is safe on USB.
void stripProof()
{
  writeSystemLineToTransports("{\"system\":\"strip_proof_start\",\"pin\":4,\"count\":16,\"brightness\":40}");
  uint32_t cols[4] = {
    strip.Color(255, 0, 0, 0), strip.Color(0, 255, 0, 0),
    strip.Color(0, 0, 255, 0), strip.Color(0, 0, 0, 255)
  };
  for (int c = 0; c < 4; c++)
  {
    for (int i = 0; i < STRIP_COUNT; i++) strip.setPixelColor(i, cols[c]);
    strip.show();
    delay(650);
  }
  stripBootIdle();
  writeSystemLineToTransports("{\"system\":\"strip_proof_done\",\"idle\":\"blue\"}");
}

// Leave a dim visible mark after boot. If this is dark, the strip is not powered,
// data is not reaching DIN, the strip direction is reversed, or this firmware is
// not what is running on the ESP.
void stripBootIdle()
{
  strip.setBrightness(18);
  for (int i = 0; i < STRIP_COUNT; i++)
  {
    strip.setPixelColor(i, strip.Color(0, 0, 255, 0));
  }
  strip.show();
}

// Backward compatibility for the v05/v06 collector LED:<n> command. It used to
// mean "onboard pixel"; for v06 it also gives the SK6812 a visible fallback.
void applyLegacyStripBrightness(uint8_t brightness)
{
  uint8_t capped = min((int)brightness, STRIP_MAX_BRIGHTNESS);
  strip.setBrightness(capped);
  if (brightness == 0)
  {
    strip.clear();
  }
  else
  {
    for (int i = 0; i < STRIP_COUNT; i++)
    {
      strip.setPixelColor(i, strip.Color(0, 0, 255, 0));
    }
  }
  strip.show();
}

// PIX:r,g,b,w,r,g,b,w,...  A full frame of up to STRIP_COUNT RGBW pixels
// (0-255 each). The decoder builds the frame; the body only renders it.
void applyPixels(const String& csv)
{
  strip.setBrightness(STRIP_MAX_BRIGHTNESS);
  String s = csv;
  s.trim();
  s += ",";                          // sentinel so the final value flushes
  uint8_t rgbw[4] = {0, 0, 0, 0};
  int ch = 0, px = 0, start = 0;
  for (int i = 0; i < (int)s.length() && px < STRIP_COUNT; i++)
  {
    if (s[i] != ',') continue;
    rgbw[ch] = (uint8_t)constrain(s.substring(start, i).toInt(), 0, 255);
    start = i + 1;
    if (++ch == 4)
    {
      strip.setPixelColor(px++, strip.Color(rgbw[0], rgbw[1], rgbw[2], rgbw[3]));
      ch = 0;
    }
  }
  while (px < STRIP_COUNT)
  {
    strip.setPixelColor(px++, 0);
  }
  strip.show();
}

// ---- MAX98357A amp emitter (I2S1) -----------------------------------------
void ampSetup()
{
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = AMP_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 256,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };
  i2s_pin_config_t pins = {
    .mck_io_num = I2S_PIN_NO_CHANGE,
    .bck_io_num = AMP_BCLK_PIN,
    .ws_io_num = AMP_LRC_PIN,
    .data_out_num = AMP_DIN_PIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
  i2s_driver_install(AMP_PORT, &cfg, 0, NULL);
  i2s_set_pin(AMP_PORT, &pins);
  i2s_zero_dma_buffer(AMP_PORT);
}

// Write `ms` of silence: settles the clock after driver churn and drains a
// tone's tail so it is not chopped (a chop is an end click).
void ampSilence(int ms)
{
  int16_t z[256] = {0};
  int total = (AMP_SAMPLE_RATE * ms) / 1000;
  int done = 0;
  while (done < total)
  {
    int n = min(256, total - done);
    size_t written = 0;
    i2s_write(AMP_PORT, z, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
  }
}

// Play a faded tone on the amp. The mic (I2S0) is uninstalled for the tone and
// reinstalled after: do not listen while speaking. Recipe proven in the bench
// smoke test (warm first, ~10 ms fades, drain the tail). Do not rediscover it.
void playTone(float freq, int ms, float vol)
{
#if ENABLE_MIC
  i2s_driver_uninstall(I2S_PORT);        // give the amp the I2S subsystem
#endif
  ampSilence(220);                       // warm the amp after I2S churn
  const float dt = 2.0f * (float)M_PI * freq / AMP_SAMPLE_RATE;
  const int total = (AMP_SAMPLE_RATE * ms) / 1000;
  const int fade = AMP_SAMPLE_RATE / 30;  // slower fade avoids start fuzz/clicks
  const float amp = TONE_AMP * constrain(vol, 0.0f, 1.0f);
  int16_t buf[256];
  int done = 0;
  while (done < total)
  {
    int n = min(256, total - done);
    for (int i = 0; i < n; i++)
    {
      int idx = done + i;
      float env = 1.0f;
      if (idx < fade) env = (float)idx / fade;
      else if (idx > total - fade) env = (float)(total - idx) / fade;
      buf[i] = (int16_t)(amp * 32767.0f * env * sinf(tonePhase));
      tonePhase += dt;
      if (tonePhase > 2.0f * (float)M_PI) tonePhase -= 2.0f * (float)M_PI;
    }
    size_t written = 0;
    i2s_write(AMP_PORT, buf, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
  }
  ampSilence(150);                       // drain the faded tail (no end click)
#if ENABLE_MIC
  setupI2SMic();                         // reinstall the mic for listening
#endif
}

// Configure the I2S peripheral for the INMP441 (receive, mono left channel).
void setupI2SMic()
{
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = I2S_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = I2S_SAMPLE_COUNT,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .mck_io_num = I2S_PIN_NO_CHANGE,     // master clock not used by the INMP441
    .bck_io_num = I2S_SCK_PIN,
    .ws_io_num = I2S_WS_PIN,
    .data_out_num = I2S_PIN_NO_CHANGE,   // mic is input only
    .data_in_num = I2S_SD_PIN
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  i2s_zero_dma_buffer(I2S_PORT);
}

// Drain the whole I2S buffer each call so we always measure current audio with
// no backlog, then return the DC-removed RMS (the AC amplitude of the sound).
// Also records sample count and min/max for diagnostics.
float readSoundRms()
{
  double  sum   = 0.0;
  double  sumSq = 0.0;
  long    count = 0;
  int32_t mn    = 2147483647;
  int32_t mx    = -2147483648;
  micFirstN = 0;

  while (true)
  {
    size_t bytesRead = 0;
    esp_err_t res = i2s_read(I2S_PORT, i2sSamples, sizeof(i2sSamples), &bytesRead, 0);
    if (res != ESP_OK || bytesRead == 0)
    {
      break;
    }

    int samples = bytesRead / sizeof(int32_t);
    for (int i = 0; i < samples; i++)
    {
      int32_t s = i2sSamples[i] >> 8;   // signed 24-bit sample
      if (micFirstN < 8) micFirst[micFirstN++] = s;
      if (s < mn) mn = s;
      if (s > mx) mx = s;
      double v = (double)s;
      sum   += v;
      sumSq += v * v;
      count++;
    }

    if (bytesRead < sizeof(i2sSamples))
    {
      break;
    }
  }

  micCount = count;
  if (count == 0)
  {
    micMin = 0;
    micMax = 0;
    return lastSoundRms;
  }
  micMin = mn;
  micMax = mx;

  double mean     = sum / count;
  double variance = (sumSq / count) - (mean * mean);
  if (variance < 0.0) variance = 0.0;

  lastSoundRms = (float)sqrt(variance);
  return lastSoundRms;
}

void setup()
{
  Serial.begin(115200);
  delay(1000);

  pixel.begin();
  pixel.setBrightness(20);
  pixel.clear();
  pixel.show();

  setupWifi();

#if (ENABLE_LIGHT || ENABLE_MOTION || ENABLE_WEATHER)
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  scanI2C();
#endif
#if ENABLE_LIGHT
  setupLight();
#endif
#if ENABLE_MOTION
  setupIMU();
#endif
#if ENABLE_WEATHER
  setupBME();
#endif

#if ENABLE_MIC
  setupI2SMic();
#endif
#if ENABLE_STRIP
  setupStrip();
#endif
#if ENABLE_VOICE
  ampSetup();
#endif
#if ENABLE_STRIP
  stripProof();              // boot proof: R, G, B, W across all pixels
#endif
#if (ENABLE_VOICE && ENABLE_BOOT_CHIRP)
  playTone(523.0f, 120, 1.0f);   // boot chirp = amp alive
#endif

  String startLine = "{\"system\":\"creature body node v06 started\"";
#if ENABLE_LIGHT
  startLine += ",\"light_ready\":";
  startLine += lightReady ? "true" : "false";
  startLine += ",\"light_addr\":\"0x";
  startLine += String(lightAddr, HEX);
  startLine += "\"";
#endif
#if ENABLE_MOTION
  startLine += ",\"imu_ready\":";
  startLine += imuReady ? "true" : "false";
#endif
#if ENABLE_WEATHER
  startLine += ",\"bme_ready\":";
  startLine += bmeReady ? "true" : "false";
#endif
#if ENABLE_STRIP
  startLine += ",\"strip\":true";
#endif
#if ENABLE_VOICE
  startLine += ",\"voice\":true";
#endif
  startLine += ",\"mic\":";
  startLine += ENABLE_MIC ? "true" : "false";
  startLine += ",\"wifi_enabled\":";
  startLine += wifiEnabled ? "true" : "false";
  startLine += "}";
  writeSystemLineToTransports(startLine);
}

void loop()
{
  unsigned long now = millis();
  serviceWifi();
  readSerialCommands();
  readWifiCommands();

  if (now - lastSampleMs < SAMPLE_INTERVAL_MS)
  {
    return;
  }
  lastSampleMs = now;

#if ENABLE_LIGHT
  float lightLux = lightReady ? lightMeter.readLightLevel() : -1.0f;
  if (lightReady && lightLux < 0.0f)
  {
    // Negative means the read failed mid-transfer, almost always a loose
    // contact. Drop to "not ready" so the status block re-inits when it returns.
    lightReady = false;
  }
#endif
#if ENABLE_MIC
  float soundRms = readSoundRms();
#endif
#if ENABLE_MOTION
  float motion = readMotion();
#endif
#if ENABLE_WEATHER
  float tempC = 0.0f, pressureHpa = 0.0f;
  readWeather(tempC, pressureHpa);
#endif

  String sampleLine = "{\"time_ms\":";
  sampleLine += now;
#if ENABLE_LIGHT
  sampleLine += ",\"light_lux\":";
  sampleLine += String(lightLux, 1);
#endif
#if ENABLE_MIC
  sampleLine += ",\"sound_rms\":";
  sampleLine += String(soundRms, 1);
#endif
#if ENABLE_MOTION
  sampleLine += ",\"motion\":";
  sampleLine += String(motion, 4);
#endif
#if ENABLE_WEATHER
  sampleLine += ",\"temp_c\":";
  sampleLine += String(tempC, 2);
  sampleLine += ",\"pressure_hpa\":";
  sampleLine += String(pressureHpa, 1);
#endif
  sampleLine += "}";
  writeLineToTransports(sampleLine);

  // ---- STATUS (temporary bring-up diagnostics) -----------------------------
  if (now - lastStatusMs >= STATUS_INTERVAL_MS)
  {
    lastStatusMs = now;

#if ENABLE_LIGHT
    if (!lightReady)
    {
      scanI2C();
      setupLight();
    }
#endif

    String statusLine = "{\"system\":\"status\"";
#if ENABLE_LIGHT
    statusLine += ",\"light_ready\":";
    statusLine += lightReady ? "true" : "false";
    statusLine += ",\"light_addr\":\"0x";
    statusLine += String(lightAddr, HEX);
    statusLine += "\",\"i2c_found\":";
    statusLine += i2cFoundCount;
#endif
#if ENABLE_MIC
    statusLine += ",\"mic_n\":";
    statusLine += micCount;
    statusLine += ",\"mic_min\":";
    statusLine += micMin;
    statusLine += ",\"mic_max\":";
    statusLine += micMax;
    statusLine += ",\"mic_s\":[";
    for (int i = 0; i < micFirstN; i++)
    {
      if (i) statusLine += ",";
      statusLine += micFirst[i];
    }
    statusLine += "]";
#endif
    statusLine += "}";
    writeSystemLineToTransports(statusLine);
  }
}
