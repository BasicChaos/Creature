#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include <BH1750.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <driver/i2s.h>
#include <esp_sleep.h>
#include <math.h>
#include <SPI.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeSansBold9pt7b.h>

#if __has_include("creature_wifi_secrets.h")
#include "creature_wifi_secrets.h"
#endif

// ---------------------------------------------------------------------------
// Creature body node firmware (v07.0: the v06 body plus air sensors, e-paper
// and a battery fuel gauge)
// Board: ESP32-S3-DevKitC-1 style, N16R8 module
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
// After each VOX tone it also sends one line saying what the mic heard while the
// tone played, measured at the tone's own pitch:
//   {"vox":{"freq":350.7,"ms":400,"vol":0.75,"heard":..,"level":..,"room":..,"room_heard":..,"n":..}}
//
// A temporary "status" line prints every 2 seconds for bring-up debugging.
// Accepts "LED:<0-255>\n" for the onboard NeoPixel over USB or TCP, mirrored
// to the SK6812 strip as a v06 fallback. The full strip output is PIX:.
//
// Low battery (a reflex of the body, it protects the cell): when the cell stays
// at or under BATTERY_WARN_V the strip's brightness is capped, and at or under
// BATTERY_SLEEP_V the body goes dark, writes a last line to the e-paper and
// deep-sleeps. It wakes every BATTERY_SLEEP_S to read the gauge and stays
// asleep until the cell is charging or back above BATTERY_RESUME_V.
// "BAT:<volts>" is a bench test: for two minutes the reflex acts on that
// voltage instead of the cell's. The streamed battery_v stays the real one.
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
#define ENABLE_AIR      1   // SCD4x (I2C 0x62) and SGP41 (I2C 0x59), stream "co2_ppm",
                            // "air_temp_c", "humidity_pct", "voc_raw" and "nox_raw"
#define ENABLE_BATTERY  1   // MAX17048 fuel gauge (I2C 0x36), stream "battery_v",
                            // "battery_pct" and "battery_rate"
#define ENABLE_PAPER    1   // Waveshare 2.13inch e-Paper HAT V4 (SPI), EPD: command
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

// ---- Air senses: SCD4x CO2 (I2C 0x62) and SGP41 VOC / NOx (I2C 0x59) -------
// Raw readings only. The body reports them; nothing here interprets them.
#define SCD_ADDR 0x62
#define SGP_ADDR 0x59
#define AIR_INTERVAL_MS      1000   // the SGP41 is meant to be read once a second
#define SGP_MEASURE_MS       50     // time the SGP41 needs between command and read
#define SGP_CONDITIONING_S   10     // the datasheet's limit; longer harms the NOx pixel
bool     scdReady = false;
bool     sgpReady = false;
bool     haveCo2 = false;          // false until the SCD4x's first reading (about 5 s)
bool     haveVoc = false;
bool     haveNox = false;          // false through the SGP41's conditioning
uint16_t airCo2Ppm = 0;
float    airTempC = 0.0f;
float    airHumidity = 0.0f;
uint16_t airVocRaw = 0;
uint16_t airNoxRaw = 0;
int      sgpSeconds = 0;
bool     sgpPending = false;       // a command is out, its answer not yet read
bool     sgpPendingConditioning = false;
unsigned long sgpSentMs = 0;
unsigned long lastAirMs = 0;

// ---- Battery: MAX17048 fuel gauge (I2C 0x36) -------------------------------
// The gauge sits in line between the LiPo and the PowerBoost and is powered by
// the cell, so it drops off the bus when the cell is out. Raw readings only.
#define GAUGE_ADDR          0x36
#define GAUGE_REG_VCELL     0x02   // cell voltage, 78.125 uV per count
#define GAUGE_REG_SOC       0x04   // state of charge, 1/256 % per count
#define GAUGE_REG_CRATE     0x16   // charge rate, signed, 0.208 %/hr per count
#define GAUGE_INTERVAL_MS   1000
#define GAUGE_RETRY_MS      10000  // how often to look for a gauge that is missing
// The low-battery reflex acts on cell voltage, not on the gauge's percentage,
// which reached 2 % with four hours of running left (run to flat, 7 October
// 2026). On that run the cell slid slowly down to 3.42 V and then fell off:
// about 55 minutes were left at 3.4 V, 20 at 3.3 V, and nothing stopped it
// until the cell's own protection at about 2.4 V.
#define BATTERY_WARN_V        3.40f  // at or under this, cap the strip
#define BATTERY_WARN_CLEAR_V  3.50f  // back above this, lift the cap
#define BATTERY_SLEEP_V       3.30f  // at or under this, go dark and deep-sleep
#define BATTERY_RESUME_V      3.60f  // asleep: wake for good at or above this
#define BATTERY_CHARGING_RATE 2.0f   // %/hr: above this the cell is on the charger
#define BATTERY_WARN_AFTER_S  10     // seconds the voltage must stay low
#define BATTERY_SLEEP_AFTER_S 30
#define BATTERY_SLEEP_S       300    // deep-sleep time between looks at the gauge
#define BATTERY_VALID_MIN_V   2.0f   // the gauge reads 0 V for its first half minute
#define BATTERY_VALID_MAX_V   4.6f
#define BATTERY_TEST_MS       120000 // how long a BAT: test voltage stands
#define BATTERY_TEST_SLEEP_S  20     // a test sleeps in short steps
#define STRIP_LOW_BATTERY_BRIGHTNESS 12
bool  gaugeReady = false;
bool  haveBattery = false;         // false until a full reading has come back
bool  batteryLow = false;          // the strip cap is on
int   batteryWarnSeconds = 0;
int   batterySleepSeconds = 0;
float batteryTestVolts = 0.0f;     // a BAT: test voltage, 0 when there is none
uint32_t batteryWokeLooks = 0;     // looks at the gauge in the sleep this boot ended
unsigned long batteryTestMs = 0;
// Kept through deep sleep.
RTC_DATA_ATTR bool     batteryAsleep = false;
RTC_DATA_ATTR uint32_t batterySleeps = 0;       // looks at the gauge this sleep
RTC_DATA_ATTR float    batteryRtcTestVolts = 0.0f;
RTC_DATA_ATTR int      batteryRtcTestWakes = 0; // wakes the test voltage still stands for
float batteryVolts = 0.0f;
float batteryPct = 0.0f;
float batteryRate = 0.0f;          // %/hr: positive charging, negative discharging
unsigned long lastGaugeMs = 0;

// ---- e-paper: Waveshare 2.13inch e-Paper HAT V4 (SPI, 250 x 122) -----------
// It shows whatever text the collector sends: two columns of five short lines.
//   EPD:left1;left2;left3;left4;left5|right1;right2;right3;right4;right5
// A redraw takes about half a second, and a full one about four, so the panel
// has a task of its own and the sample loop never waits for it.
// Wiring, in the order of the HAT's own connector, on the ESP's free edge:
//   DIN 1, CLK 2, CS 42, DC 41, RST 40, BUSY 39. VCC to 3V3, GND to GND.
#define EPD_DIN   1
#define EPD_CLK   2
#define EPD_CS    42
#define EPD_DC    41
#define EPD_RST   40
#define EPD_BUSY  39
#define PAPER_LINES          5
#define PAPER_LINE_CHARS     20
#define PAPER_MIN_GAP_MS     60000   // never redraw more often than this
#define PAPER_FULL_EVERY     10      // every Nth redraw is a full one, to clear ghosting
// GxEPD2_213_BN also drives this panel, but its partial refresh left faint grey
// text and heavy ghosting on the V4.
#define EPD_DRIVER GxEPD2_213_GDEY0213B74
GxEPD2_BW<EPD_DRIVER, EPD_DRIVER::HEIGHT>
    paper(EPD_DRIVER(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));
SemaphoreHandle_t paperLock = NULL;
char paperText[2][PAPER_LINES][PAPER_LINE_CHARS];   // [column][line], guarded by paperLock
volatile bool paperWanted = false;                  // new text is waiting to be drawn
volatile bool paperUrgent = false;                  // draw it now, in full (the last text before sleep)
volatile bool paperUrgentDone = false;
volatile bool paperReportPending = false;           // a redraw finished; tell the collector
volatile bool paperReportFull = false;
volatile unsigned long paperReportMs = 0;

// ---- SK6812 RGBW strip emitter (GPIO 4, 16 px) ----------------------------
#define STRIP_PIN            4
#define STRIP_COUNT          16
#define STRIP_MAX_BRIGHTNESS 40   // current cap on USB/PowerBoost; never all-white
uint8_t stripCap = STRIP_MAX_BRIGHTNESS;   // lowered while the battery is low
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
// On a weak signal a join was measured at 15.7 s (4 October 2026). Retrying
// sooner than that cuts every attempt off before it can finish.
const unsigned long WIFI_RETRY_INTERVAL_MS = 30000;

String serialCommand = "";
String wifiCommand = "";

// Forward declarations
// ---- Hearing its own tone --------------------------------------------------
// A running measurement of mic samples: the plain level, and how much of it
// sits at one pitch.
struct EarSum
{
  double sum = 0.0, sumSq = 0.0;   // level
  double i = 0.0, q = 0.0;         // correlation with the pitch
  double c = 0.0, s = 0.0;         // sums of the reference, to take the mic's DC back out
  long   n = 0;
};

// What playTone listens with while it writes to the amp.
struct ToneListen
{
  float  freq;       // the pitch being played
  long   index;      // mic samples seen since listening began
  long   winStart;   // the stretch of mic samples that is safely inside the tone
  long   winEnd;
  EarSum tone;
};

void  setRgb(uint8_t red, uint8_t green, uint8_t blue);
void  scanI2C();
void  setupLight();
void  setupIMU();
float readMotion();
void  setupBME();
void  readWeather(float& tempC, float& pressureHpa);
void  setupAir();
void  setupPaper();
void  setPaperText(const String& text);
void  reportPaper();
void  serviceAir(unsigned long now);
void  batteryReflex();
void  batteryWakeCheck();
void  setupStrip();
void  stripProof();
void  stripBootIdle();
void  applyLegacyStripBrightness(uint8_t brightness);
void  applyPixels(const String& csv);
void  ampSetup();
void  ampSilence(int ms, ToneListen* listen = NULL);
void  playTone(float freq, int ms, float vol, int attackMs = 10, int releaseMs = 10,
                float h2 = 0.0f, float h3 = 0.0f);
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
#if ENABLE_PAPER
  else if (command.startsWith("EPD:"))
  {
    setPaperText(command.substring(4));
  }
#endif
#if ENABLE_BATTERY
  else if (command.startsWith("BAT:"))
  {
    // Bench test of the low-battery reflex: act on this voltage for two minutes.
    batteryTestVolts = command.substring(4).toFloat();
    batteryTestMs = millis();
    String response = "{\"system\":\"battery_test\",\"volts\":";
    response += String(batteryTestVolts, 2);
    response += "}";
    writeSystemLineToTransports(response);
  }
#endif
#if ENABLE_VOICE
  else if (command.startsWith("VOX:"))
  {
    // VOX:freq,ms[,vol[,attack_ms,release_ms[,h2,h3]]]
    //   freq in Hz, ms duration, vol 0-1. The rest shape the tone and may be
    //   left off: how long it takes to rise and to die away (10 ms each when
    //   not given, the plain beep), and how much of the second and third
    //   overtone is mixed in (0 to 1 each, none when not given).
    String args = command.substring(4);
    float v[7] = {0.0f, 150.0f, 1.0f, 10.0f, 10.0f, 0.0f, 0.0f};
    int start = 0;
    for (int i = 0; i < 7 && start <= (int)args.length(); i++)
    {
      int comma = args.indexOf(',', start);
      String part = (comma >= 0) ? args.substring(start, comma) : args.substring(start);
      part.trim();
      if (part.length() > 0) v[i] = part.toFloat();
      if (comma < 0) break;
      start = comma + 1;
    }
    if (v[0] > 0.0f && v[1] > 0.0f) playTone(v[0], (int)v[1], v[2], (int)v[3], (int)v[4], v[5], v[6]);
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
  WiFi.setSleep(false); // no modem power-save: keeps current steady on boot/connect.
                        // (Previously blamed for amp distortion via the 3V3 rail; real
                        // cause was amp VIN wired to 3V3 — fixed, VIN now on 5V.)
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
    line += ",\"rssi\":";
    line += WiFi.RSSI();
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

// ---- Air senses ------------------------------------------------------------
// The Sensirion parts speak a 16-bit command and a CRC after every data word.
uint8_t sensirionCrc(uint8_t hi, uint8_t lo)
{
  uint8_t crc = 0xFF;
  uint8_t bytes[2] = {hi, lo};
  for (int i = 0; i < 2; i++)
  {
    crc ^= bytes[i];
    for (int b = 0; b < 8; b++)
    {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

bool sensirionCommand(uint8_t addr, uint16_t cmd, const uint16_t* args = NULL, int count = 0)
{
  Wire.beginTransmission(addr);
  Wire.write((uint8_t)(cmd >> 8));
  Wire.write((uint8_t)(cmd & 0xFF));
  for (int i = 0; i < count; i++)
  {
    uint8_t hi = args[i] >> 8;
    uint8_t lo = args[i] & 0xFF;
    Wire.write(hi);
    Wire.write(lo);
    Wire.write(sensirionCrc(hi, lo));
  }
  return Wire.endTransmission() == 0;
}

bool sensirionRead(uint8_t addr, uint16_t* words, int count)
{
  int wanted = count * 3;
  if (Wire.requestFrom((int)addr, wanted) != wanted)
  {
    return false;
  }
  bool ok = true;
  for (int i = 0; i < count; i++)
  {
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    uint8_t crc = Wire.read();
    if (crc != sensirionCrc(hi, lo))
    {
      ok = false;
    }
    words[i] = ((uint16_t)hi << 8) | lo;
  }
  return ok;
}

bool i2cPresent(uint8_t addr)
{
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void setupAir()
{
  if (i2cPresent(SCD_ADDR))
  {
    // It may still be measuring from before a reset, and then ignores the rest.
    sensirionCommand(SCD_ADDR, 0x3F86);              // stop periodic measurement
    delay(500);
    scdReady = sensirionCommand(SCD_ADDR, 0x21B1);   // start periodic measurement
  }
  sgpReady = i2cPresent(SGP_ADDR);
}

// Take the SCD4x's reading if a new one is waiting (one arrives every 5 s).
void pollScd()
{
  uint16_t w[3];
  if (!sensirionCommand(SCD_ADDR, 0xE4B8))           // data ready?
  {
    return;
  }
  delay(1);
  if (!sensirionRead(SCD_ADDR, w, 1) || (w[0] & 0x07FF) == 0)
  {
    return;
  }
  if (!sensirionCommand(SCD_ADDR, 0xEC05))           // read measurement
  {
    return;
  }
  delay(1);
  if (!sensirionRead(SCD_ADDR, w, 3))
  {
    return;
  }
  airCo2Ppm = w[0];
  airTempC = -45.0f + 175.0f * w[1] / 65535.0f;
  airHumidity = 100.0f * w[2] / 65535.0f;
  haveCo2 = true;
}

// Called every pass of loop(). Once a second it asks the SCD4x for a new
// reading and starts an SGP41 measurement; the SGP41's answer is collected on
// a later pass, so the loop never waits out its 50 ms.
void serviceAir(unsigned long now)
{
  if (sgpPending)
  {
    if (now - sgpSentMs < SGP_MEASURE_MS)
    {
      return;
    }
    sgpPending = false;
    uint16_t w[2] = {0, 0};
    if (sensirionRead(SGP_ADDR, w, sgpPendingConditioning ? 1 : 2))
    {
      airVocRaw = w[0];
      haveVoc = true;
      if (!sgpPendingConditioning)
      {
        airNoxRaw = w[1];
        haveNox = true;
      }
    }
    return;
  }

  if (now - lastAirMs < AIR_INTERVAL_MS)
  {
    return;
  }
  lastAirMs = now;

  if (scdReady)
  {
    pollScd();
  }
  if (sgpReady)
  {
    // The SGP41 corrects for humidity and temperature. It gets the SCD4x's
    // when there are any, the datasheet defaults (50 %, 25 C) when not.
    uint16_t args[2] = {0x8000, 0x6666};
    if (haveCo2)
    {
      args[0] = (uint16_t)(constrain(airHumidity, 0.0f, 100.0f) * 65535.0f / 100.0f);
      args[1] = (uint16_t)((constrain(airTempC, -45.0f, 130.0f) + 45.0f) * 65535.0f / 175.0f);
    }
    sgpPendingConditioning = sgpSeconds < SGP_CONDITIONING_S;
    if (sensirionCommand(SGP_ADDR, sgpPendingConditioning ? 0x2612 : 0x2619, args, 2))
    {
      sgpPending = true;
      sgpSentMs = now;
      sgpSeconds++;
    }
  }
}

// ---- Battery ---------------------------------------------------------------
bool gaugeRead16(uint8_t reg, uint16_t& value)
{
  Wire.beginTransmission(GAUGE_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0)
  {
    return false;
  }
  if (Wire.requestFrom((int)GAUGE_ADDR, 2) != 2)
  {
    return false;
  }
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  value = ((uint16_t)hi << 8) | lo;
  return true;
}

void setupGauge()
{
  gaugeReady = i2cPresent(GAUGE_ADDR);
}

// Called every pass of loop(). Once a second it reads the cell's voltage,
// charge and charge rate. A failed read drops the battery from the sample
// lines until the gauge answers again.
void serviceGauge(unsigned long now)
{
  if (now - lastGaugeMs < (gaugeReady ? GAUGE_INTERVAL_MS : GAUGE_RETRY_MS))
  {
    return;
  }
  lastGaugeMs = now;

  if (!gaugeReady)
  {
    gaugeReady = i2cPresent(GAUGE_ADDR);
    return;
  }

  uint16_t vcell = 0, soc = 0, crate = 0;
  if (!gaugeRead16(GAUGE_REG_VCELL, vcell) || !gaugeRead16(GAUGE_REG_SOC, soc) ||
      !gaugeRead16(GAUGE_REG_CRATE, crate))
  {
    gaugeReady = false;
    haveBattery = false;
    return;
  }
  float volts = vcell * 78.125e-6f;
  if (volts < BATTERY_VALID_MIN_V || volts > BATTERY_VALID_MAX_V)
  {
    // Not a reading. The gauge answers with zeros for about half a minute
    // after the cell comes back.
    haveBattery = false;
    return;
  }
  batteryVolts = volts;
  batteryPct = soc / 256.0f;
  batteryRate = (int16_t)crate * 0.208f;
  haveBattery = true;
  batteryReflex();
}

// Read the gauge once, outside the normal loop. False when it gives no reading.
bool gaugeReadOnce(float& volts, float& rate)
{
  uint16_t vcell = 0, crate = 0;
  if (!gaugeRead16(GAUGE_REG_VCELL, vcell) || !gaugeRead16(GAUGE_REG_CRATE, crate))
  {
    return false;
  }
  volts = vcell * 78.125e-6f;
  rate = (int16_t)crate * 0.208f;
  return volts >= BATTERY_VALID_MIN_V && volts <= BATTERY_VALID_MAX_V;
}

// Go dark and deep-sleep until the next look at the gauge. Does not return.
void batterySleep(float volts, bool test)
{
  String line = "{\"system\":\"battery_sleep\",\"battery_v\":";
  line += String(volts, 3);
  line += ",\"wake_s\":";
  line += test ? BATTERY_TEST_SLEEP_S : BATTERY_SLEEP_S;
  line += test ? ",\"test\":true}" : "}";
  writeSystemLineToTransports(line);

#if ENABLE_STRIP
  strip.clear();
  strip.show();
#endif
  pixel.clear();
  pixel.show();
#if ENABLE_PAPER
  // The panel keeps its image with no power, so this stays readable.
  String text = "Battery low;";
  text += String(volts, 2);
  text += " V;Asleep|Plug in the;charger";
  setPaperText(text);
  paperUrgentDone = false;
  paperUrgent = true;
  for (int i = 0; i < 150 && !paperUrgentDone; i++)
  {
    delay(100);
  }
#endif
#if ENABLE_AIR
  if (scdReady)
  {
    sensirionCommand(SCD_ADDR, 0x3F86);   // stop measuring: it draws less
  }
#endif
  delay(200);                              // let the last line leave

  batteryAsleep = true;
  batterySleeps = 0;
  batteryRtcTestVolts = test ? volts : 0.0f;
  batteryRtcTestWakes = test ? 2 : 0;
  esp_sleep_enable_timer_wakeup((uint64_t)(test ? BATTERY_TEST_SLEEP_S : BATTERY_SLEEP_S) * 1000000ULL);
  esp_deep_sleep_start();
}

// Called once a second, after each good reading of the gauge.
void batteryReflex()
{
  bool test = batteryTestVolts > 0.0f && millis() - batteryTestMs < BATTERY_TEST_MS;
  if (!test)
  {
    batteryTestVolts = 0.0f;
  }
  float volts = test ? batteryTestVolts : batteryVolts;
  bool charging = !test && batteryRate > BATTERY_CHARGING_RATE;

  batteryWarnSeconds = (volts <= BATTERY_WARN_V && !charging) ? batteryWarnSeconds + 1 : 0;
  batterySleepSeconds = (volts <= BATTERY_SLEEP_V && !charging) ? batterySleepSeconds + 1 : 0;

  if (!batteryLow && batteryWarnSeconds >= BATTERY_WARN_AFTER_S)
  {
    batteryLow = true;
    stripCap = STRIP_LOW_BATTERY_BRIGHTNESS;
#if ENABLE_STRIP
    strip.setBrightness(stripCap);
    strip.show();
#endif
    String line = "{\"system\":\"battery_low\",\"battery_v\":";
    line += String(volts, 3);
    line += ",\"strip_cap\":";
    line += stripCap;
    line += "}";
    writeSystemLineToTransports(line);
  }
  else if (batteryLow && (volts >= BATTERY_WARN_CLEAR_V || charging))
  {
    batteryLow = false;
    stripCap = STRIP_MAX_BRIGHTNESS;
    String line = "{\"system\":\"battery_ok\",\"battery_v\":";
    line += String(volts, 3);
    line += "}";
    writeSystemLineToTransports(line);
  }

  if (batterySleepSeconds >= BATTERY_SLEEP_AFTER_S)
  {
    batterySleep(volts, test);
  }
}

// First thing at boot. After a timer wake from a battery sleep, look at the
// gauge and go straight back to sleep unless the cell is charging or has
// recovered. No reading means boot normally.
void batteryWakeCheck()
{
  if (!batteryAsleep || esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER)
  {
    batteryAsleep = false;       // a reset or a power cycle: boot normally
    batteryRtcTestWakes = 0;
    return;
  }
  batterySleeps++;
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  float volts = 0.0f, rate = 0.0f;
  bool read = false;
  for (int i = 0; i < 5 && !read; i++)
  {
    read = gaugeReadOnce(volts, rate);
    if (!read)
    {
      delay(100);
    }
  }
  bool test = batteryRtcTestWakes > 0;
  if (test)
  {
    batteryRtcTestWakes--;
    volts = batteryRtcTestVolts;
    rate = 0.0f;
    read = true;
  }
  if (read && volts < BATTERY_RESUME_V && rate <= BATTERY_CHARGING_RATE)
  {
    esp_sleep_enable_timer_wakeup((uint64_t)(test ? BATTERY_TEST_SLEEP_S : BATTERY_SLEEP_S) * 1000000ULL);
    esp_deep_sleep_start();
  }
  // Awake for good. The start line reports how many looks it took.
  batteryWokeLooks = batterySleeps;
  batteryAsleep = false;
}

// ---- e-paper ---------------------------------------------------------------
// Take new text from an EPD: command. Lines are split on ';', the two columns
// on '|'. Nothing is drawn here; the panel's task picks it up.
void setPaperText(const String& text)
{
  if (paperLock == NULL)
  {
    return;
  }
  char next[2][PAPER_LINES][PAPER_LINE_CHARS];
  memset(next, 0, sizeof(next));
  int column = 0, line = 0, at = 0;
  for (unsigned int i = 0; i < text.length(); i++)
  {
    char c = text[i];
    if (c == '|')
    {
      column++;
      line = 0;
      at = 0;
      if (column > 1)
      {
        break;
      }
    }
    else if (c == ';')
    {
      line++;
      at = 0;
    }
    else if (line < PAPER_LINES && at < PAPER_LINE_CHARS - 1 && c >= ' ' && c <= '~')
    {
      next[column][line][at++] = c;
    }
  }
  xSemaphoreTake(paperLock, portMAX_DELAY);
  if (memcmp(next, paperText, sizeof(next)) != 0)
  {
    memcpy(paperText, next, sizeof(next));
    paperWanted = true;
  }
  xSemaphoreGive(paperLock);
}

// The panel's own task. It waits for new text, then redraws: a fast partial
// refresh usually, a full one the first time and every PAPER_FULL_EVERY after.
void paperTask(void* unused)
{
  char shown[2][PAPER_LINES][PAPER_LINE_CHARS];
  int redraws = 0;
  unsigned long lastDrawMs = 0;

  SPI.begin(EPD_CLK, -1, EPD_DIN, EPD_CS);
  paper.init(0, true, 2, false);   // no serial chatter; Waveshare's short 2 ms reset pulse
  paper.setRotation(1);

  for (;;)
  {
    vTaskDelay(pdMS_TO_TICKS(250));
    bool urgent = paperUrgent;
    if (!paperWanted || (!urgent && redraws > 0 && millis() - lastDrawMs < PAPER_MIN_GAP_MS))
    {
      continue;
    }
    xSemaphoreTake(paperLock, portMAX_DELAY);
    memcpy(shown, paperText, sizeof(shown));
    paperWanted = false;
    xSemaphoreGive(paperLock);

    bool full = urgent || (redraws % PAPER_FULL_EVERY) == 0;
    if (full)
    {
      paper.setFullWindow();
    }
    else
    {
      paper.setPartialWindow(0, 0, paper.width(), paper.height());
    }
    unsigned long started = millis();
    paper.firstPage();
    do
    {
      paper.fillScreen(GxEPD_WHITE);
      paper.setTextColor(GxEPD_BLACK);
      paper.setFont(&FreeSansBold9pt7b);
      paper.drawFastVLine(paper.width() / 2, 4, paper.height() - 8, GxEPD_BLACK);
      for (int column = 0; column < 2; column++)
      {
        for (int line = 0; line < PAPER_LINES; line++)
        {
          paper.setCursor(column == 0 ? 5 : paper.width() / 2 + 7, 20 + line * 23);
          paper.print(shown[column][line]);
        }
      }
    } while (paper.nextPage());
    paper.powerOff();
    lastDrawMs = millis();
    redraws++;

    paperReportMs = lastDrawMs - started;
    paperReportFull = full;
    paperReportPending = true;
    if (urgent)
    {
      paperUrgent = false;
      paperUrgentDone = true;
    }
  }
}

void setupPaper()
{
  memset(paperText, 0, sizeof(paperText));
  paperLock = xSemaphoreCreateMutex();
  // Core 0, low priority: the sample loop runs on core 1.
  xTaskCreatePinnedToCore(paperTask, "paper", 8192, NULL, 1, NULL, 0);
}

// Called from loop(): say when a redraw has finished and how long it took. Under
// 200 ms means BUSY is not connected; over 9000 ms means it timed out.
void reportPaper()
{
  if (!paperReportPending)
  {
    return;
  }
  paperReportPending = false;
  String line = "{\"system\":\"paper\",\"refresh\":\"";
  line += paperReportFull ? "full" : "partial";
  line += "\",\"ms\":";
  line += paperReportMs;
  line += "}";
  writeSystemLineToTransports(line);
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
  uint8_t capped = min((int)brightness, (int)stripCap);
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
  strip.setBrightness(stripCap);
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

// Add mic samples to a running measurement. `index` is the position of the first
// sample since listening began; it sets the phase of the pitch they are compared
// with, so separate reads line up into one continuous measurement.
static void earAdd(EarSum& e, const int32_t* raw, int count, long index, float freq)
{
  const float twoPi = 2.0f * (float)M_PI;
  const float dphi = twoPi * freq / I2S_SAMPLE_RATE;
  float phase = fmodf(dphi * (float)index, twoPi);
  for (int k = 0; k < count; k++)
  {
    float v  = (float)(raw[k] >> 8);   // signed 24-bit sample
    float cs = cosf(phase);
    float sn = sinf(phase);
    e.sum   += v;
    e.sumSq += (double)v * v;
    e.i     += v * cs;
    e.q     += v * sn;
    e.c     += cs;
    e.s     += sn;
    phase   += dphi;
    if (phase > twoPi) phase -= twoPi;
  }
  e.n += count;
}

// DC-removed level, the same measure the 10 Hz stream reports as sound_rms.
static float earLevel(const EarSum& e)
{
  if (e.n == 0) return 0.0f;
  double mean = e.sum / e.n;
  double variance = (e.sumSq / e.n) - (mean * mean);
  return variance > 0.0 ? (float)sqrt(variance) : 0.0f;
}

// RMS of the part of the signal that sits at the pitch.
static float earPitch(const EarSum& e)
{
  if (e.n == 0) return 0.0f;
  double mean = e.sum / e.n;
  double i = e.i - mean * e.c;
  double q = e.q - mean * e.s;
  return (float)(2.0 * sqrt(i * i + q * q) / e.n / sqrt(2.0));
}

// Read whatever the mic has ready, without waiting, and keep the samples that
// fall inside the listening window.
static void listenDrain(ToneListen* listen)
{
#if ENABLE_MIC
  if (listen == NULL) return;
  while (true)
  {
    size_t bytesRead = 0;
    esp_err_t res = i2s_read(I2S_PORT, i2sSamples, sizeof(i2sSamples), &bytesRead, 0);
    if (res != ESP_OK || bytesRead == 0) break;
    int count = bytesRead / sizeof(int32_t);
    long from = max(listen->index, listen->winStart);
    long to   = min(listen->index + count, listen->winEnd);
    if (to > from)
    {
      earAdd(listen->tone, i2sSamples + (from - listen->index), (int)(to - from),
             from, listen->freq);
    }
    listen->index += count;
    if (bytesRead < sizeof(i2sSamples)) break;
  }
#endif
}

// Write `ms` of silence: settles the clock after driver churn and drains a
// tone's tail so it is not chopped (a chop is an end click). With `listen`, the
// mic is read between writes, because the tone is still sounding during both.
void ampSilence(int ms, ToneListen* listen)
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
    listenDrain(listen);
  }
}

// Play a faded tone on the amp. The mic (I2S0) stays live: it and the amp (I2S1)
// are separate I2S peripherals on separate pins, so the creature keeps listening
// while it speaks, and hears its own voice. The clean-tone recipe still holds
// (warm first, ~10 ms fades, drain the tail); do not rediscover it. The old mic
// uninstall was a wrong guess at the distortion. The real cause was amp VIN on
// 3V3, fixed by moving VIN to 5V.
// One tone. It rises over attackMs and dies away over releaseMs (a straight
// line up; on the way down a straight line for a short fade and a curve for a
// long one, which is what makes a pluck sound plucked). h2 and h3 mix in the
// second and third overtone; the whole is scaled so its peak stays where a
// plain tone's is, inside the amp's clean range.
void playTone(float freq, int ms, float vol, int attackMs, int releaseMs, float h2, float h3)
{
  const int warmMs = 40;
  const int total = (AMP_SAMPLE_RATE * ms) / 1000;

  ToneListen* listen = NULL;
#if ENABLE_MIC
  // The room just before speaking: whatever the mic buffered since its last read.
  EarSum room;
  while (true)
  {
    size_t bytesRead = 0;
    esp_err_t res = i2s_read(I2S_PORT, i2sSamples, sizeof(i2sSamples), &bytesRead, 0);
    if (res != ESP_OK || bytesRead == 0) break;
    int count = bytesRead / sizeof(int32_t);
    earAdd(room, i2sSamples, count, room.n, freq);
    if (bytesRead < sizeof(i2sSamples)) break;
  }

  // Listen only where the tone is certain to be sounding. What is written to the
  // amp comes out of the speaker up to its whole DMA ring later (8 x 256 samples),
  // so the window starts that long after the warm-up and ends where the tone
  // would end with no delay at all. Short tones leave no such stretch: for those,
  // listen from the start of the tone to its latest possible end instead.
  const long warm  = (AMP_SAMPLE_RATE * warmMs) / 1000;
  const long ring  = 8 * 256;
  const long guard = AMP_SAMPLE_RATE / 100;   // 10 ms, the length of the fades
  ToneListen ear;
  ear.freq = freq;
  ear.index = 0;
  ear.winStart = warm + ring + guard;
  ear.winEnd = warm + total - guard;
  if (ear.winEnd - ear.winStart < AMP_SAMPLE_RATE / 25)
  {
    ear.winStart = warm + guard;
    ear.winEnd = warm + total + ring;
  }
  listen = &ear;
#endif

  ampSilence(warmMs, listen);            // warm the amp before the tone
  const float dt = 2.0f * (float)M_PI * freq / AMP_SAMPLE_RATE;
  // Never under 2 ms either way, so there is no click. 10 ms is the fade from
  // the clean bench test.
  int attack = (AMP_SAMPLE_RATE * constrain(attackMs, 2, 2000)) / 1000;
  int release = (AMP_SAMPLE_RATE * constrain(releaseMs, 2, 4000)) / 1000;
  if (attack + release > total)
  {
    attack = (int)((long)total * attack / (attack + release));
    release = total - attack;
  }
  const bool curved = releaseMs > 20;
  h2 = constrain(h2, 0.0f, 1.0f);
  h3 = constrain(h3, 0.0f, 1.0f);
  const float amp = TONE_AMP * constrain(vol, 0.0f, 1.0f) / (1.0f + h2 + h3);
  int16_t buf[256];
  int done = 0;
  while (done < total)
  {
    int n = min(256, total - done);
    for (int i = 0; i < n; i++)
    {
      int idx = done + i;
      float env = 1.0f;
      if (idx < attack) env = (float)idx / attack;
      else if (idx > total - release)
      {
        env = (float)(total - idx) / release;
        if (curved) env *= env;
      }
      float wave = sinf(tonePhase);
      if (h2 > 0.0f) wave += h2 * sinf(2.0f * tonePhase);
      if (h3 > 0.0f) wave += h3 * sinf(3.0f * tonePhase);
      buf[i] = (int16_t)(amp * 32767.0f * env * wave);
      tonePhase += dt;
      if (tonePhase > 2.0f * (float)M_PI) tonePhase -= 2.0f * (float)M_PI;
    }
    size_t written = 0;
    i2s_write(AMP_PORT, buf, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
    listenDrain(listen);
  }
  ampSilence(150, listen);               // drain the faded tail (no end click)

#if ENABLE_MIC
  // What it heard of itself: the level at its own pitch while the tone played,
  // next to the same two measures of the room just before.
  String line = "{\"vox\":{\"freq\":";
  line += String(freq, 1);
  line += ",\"ms\":";
  line += ms;
  line += ",\"vol\":";
  line += String(vol, 2);
  line += ",\"heard\":";
  line += String(earPitch(ear.tone), 1);
  line += ",\"level\":";
  line += String(earLevel(ear.tone), 1);
  line += ",\"room\":";
  line += String(earLevel(room), 1);
  line += ",\"room_heard\":";
  line += String(earPitch(room), 1);
  line += ",\"n\":";
  line += ear.tone.n;
  line += "}}";
  writeLineToTransports(line);
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
#if ENABLE_BATTERY
  batteryWakeCheck();            // may go straight back to sleep
#endif
  delay(1000);

  pixel.begin();
  pixel.setBrightness(20);
  pixel.clear();
  pixel.show();

  setupWifi();

#if (ENABLE_LIGHT || ENABLE_MOTION || ENABLE_WEATHER || ENABLE_AIR || ENABLE_BATTERY)
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
#if ENABLE_AIR
  setupAir();
#endif
#if ENABLE_BATTERY
  setupGauge();
#endif
#if ENABLE_PAPER
  setupPaper();
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

  String startLine = "{\"system\":\"creature body node v07 started\"";
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
#if ENABLE_AIR
  startLine += ",\"scd_ready\":";
  startLine += scdReady ? "true" : "false";
  startLine += ",\"sgp_ready\":";
  startLine += sgpReady ? "true" : "false";
#endif
#if ENABLE_BATTERY
  startLine += ",\"gauge_ready\":";
  startLine += gaugeReady ? "true" : "false";
  // Looks at the gauge during the battery sleep this boot ended, 0 for none.
  startLine += ",\"battery_woke\":";
  startLine += batteryWokeLooks;
#endif
#if ENABLE_PAPER
  startLine += ",\"paper\":true";
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
#if ENABLE_AIR
  serviceAir(now);
#endif
#if ENABLE_BATTERY
  serviceGauge(now);
#endif
#if ENABLE_PAPER
  reportPaper();
#endif

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
#if ENABLE_AIR
  // Each appears only once its sensor has given a real reading.
  if (haveCo2)
  {
    sampleLine += ",\"co2_ppm\":";
    sampleLine += airCo2Ppm;
    sampleLine += ",\"air_temp_c\":";
    sampleLine += String(airTempC, 2);
    sampleLine += ",\"humidity_pct\":";
    sampleLine += String(airHumidity, 1);
  }
  if (haveVoc)
  {
    sampleLine += ",\"voc_raw\":";
    sampleLine += airVocRaw;
  }
  if (haveNox)
  {
    sampleLine += ",\"nox_raw\":";
    sampleLine += airNoxRaw;
  }
#endif
#if ENABLE_BATTERY
  if (haveBattery)
  {
    sampleLine += ",\"battery_v\":";
    sampleLine += String(batteryVolts, 3);
    sampleLine += ",\"battery_pct\":";
    sampleLine += String(batteryPct, 1);
    sampleLine += ",\"battery_rate\":";
    sampleLine += String(batteryRate, 1);
  }
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
    if (wifiEnabled)
    {
      // Signal strength in dBm, or null while not joined. Around -60 is good,
      // below -85 is too weak to hold a connection.
      statusLine += ",\"wifi_rssi\":";
      if (WiFi.status() == WL_CONNECTED)
      {
        statusLine += WiFi.RSSI();
      }
      else
      {
        statusLine += "null";
      }
    }
    statusLine += "}";
    writeSystemLineToTransports(statusLine);
  }
}
