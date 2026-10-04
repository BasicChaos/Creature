// Creature bench test: the air senses and the e-paper, on a bare ESP32-S3.
//   SCD40/SCD41  CO2, temperature, humidity   (I2C 0x62)
//   SGP41        VOC and NOx                  (I2C 0x59)
//   Waveshare 2.13inch e-Paper HAT V4         (SPI, 250 x 122, SSD1680)
//
// Each part is brought up on its own and reported on its own, so one missing
// part does not stop the other two.
//
// Wiring (all on the 3V3 rail, common ground):
//   SCD4x   VDD -> 3V3   GND -> GND   SDA -> GPIO 8   SCL -> GPIO 9
//   SGP41   VIN -> 3V3   GND -> GND   SDA -> GPIO 8   SCL -> GPIO 9
//           (leave the SGP41 "3.3V" pin open, it is an output)
//   e-paper VCC  grey   -> 3V3
//           GND  brown  -> GND
//           DIN  blue   -> GPIO 1
//           CLK  yellow -> GPIO 2
//           CS   orange -> GPIO 42
//           DC   green  -> GPIO 41
//           RST  white  -> GPIO 40
//           BUSY purple -> GPIO 39
//   The HAT's BS switch must sit at 0 (4-line SPI).
// The six signal pins sit side by side on the ESP's free edge, in the order of
// the HAT's own connector: 1, 2, 42, 41, 40, 39. None is used by the v06 body.
//
// Pass:
//   SCD4x  first reading after about 5 s; CO2 400-2000 ppm indoors, climbs
//          within a few readings when you breathe on it.
//   SGP41  self-test ok; raw VOC in the tens of thousands, drops when you
//          breathe on it or hold alcohol near it. The VOC index reads 0 for
//          the first 45 s or so, then settles around 100. The NOx index needs
//          several minutes and settles around 1.
//   paper  a full refresh takes roughly 1 to 5 s and the readings show. It
//          redraws every 15 s with a fast partial refresh, and every tenth
//          redraw is a full one to clear ghosting. A refresh that returns at
//          once, or "Busy Timeout!", means wiring.
//
// The Sensirion parts are read with raw I2C (16-bit command, CRC per word),
// like the IMU in the main firmware. Only the gas index maths is a library.

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>
#include <Fonts/FreeMonoBold18pt7b.h>
#include <VOCGasIndexAlgorithm.h>
#include <NOxGasIndexAlgorithm.h>

#define I2C_SDA   8
#define I2C_SCL   9
#define SCD_ADDR  0x62
#define SGP_ADDR  0x59

#define EPD_CS    42
#define EPD_DIN   1
#define EPD_CLK   2
#define EPD_DC    41
#define EPD_RST   40
#define EPD_BUSY  39

#define SGP_CONDITIONING_S   10    // the datasheet's limit; longer harms the NOx pixel
#define PAPER_EVERY_MS       15000
#define PAPER_FULL_EVERY     10    // every Nth redraw is a full refresh, to clear ghosting

// Panel driver. GxEPD2_213_BN also drives this panel, but its partial refresh
// left faint grey text and heavy ghosting on the V4.
#define EPD_DRIVER GxEPD2_213_GDEY0213B74

GxEPD2_BW<EPD_DRIVER, EPD_DRIVER::HEIGHT>
    display(EPD_DRIVER(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

VOCGasIndexAlgorithm vocAlgorithm;
NOxGasIndexAlgorithm noxAlgorithm;

bool scdReady = false;
bool sgpReady = false;

bool     haveCo2 = false;
uint16_t co2Ppm = 0;
float    tempC = 0.0f;
float    humidity = 0.0f;

int      sgpSeconds = 0;
uint16_t rawVoc = 0;
uint16_t rawNox = 0;
int32_t  vocIndex = 0;
int32_t  noxIndex = 0;

unsigned long lastTick = 0;
unsigned long lastPaper = 0;
int paperCount = 0;

// ---- Sensirion I2C helpers -------------------------------------------------
uint8_t sensirionCrc(uint8_t hi, uint8_t lo) {
  uint8_t crc = 0xFF;
  uint8_t bytes[2] = {hi, lo};
  for (int i = 0; i < 2; i++) {
    crc ^= bytes[i];
    for (int b = 0; b < 8; b++) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

bool sensirionCommand(uint8_t addr, uint16_t cmd, const uint16_t *args = nullptr, int count = 0) {
  Wire.beginTransmission(addr);
  Wire.write((uint8_t)(cmd >> 8));
  Wire.write((uint8_t)(cmd & 0xFF));
  for (int i = 0; i < count; i++) {
    uint8_t hi = args[i] >> 8;
    uint8_t lo = args[i] & 0xFF;
    Wire.write(hi);
    Wire.write(lo);
    Wire.write(sensirionCrc(hi, lo));
  }
  return Wire.endTransmission() == 0;
}

bool sensirionRead(uint8_t addr, uint16_t *words, int count) {
  int wanted = count * 3;
  if (Wire.requestFrom((int)addr, wanted) != wanted) return false;
  bool ok = true;
  for (int i = 0; i < count; i++) {
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    uint8_t crc = Wire.read();
    if (crc != sensirionCrc(hi, lo)) ok = false;
    words[i] = ((uint16_t)hi << 8) | lo;
  }
  return ok;
}

bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void printSerialNumber(const uint16_t *w) {
  Serial.printf("serial %04X%04X%04X", w[0], w[1], w[2]);
}

// ---- SCD4x ----------------------------------------------------------------
bool scdBegin() {
  if (!i2cPresent(SCD_ADDR)) {
    Serial.println("[scd4x] not found at 0x62 - check SDA/SCL, 3V3, GND.");
    return false;
  }
  // It may still be measuring from before a reset, and then ignores the rest.
  sensirionCommand(SCD_ADDR, 0x3F86);   // stop periodic measurement
  delay(500);

  uint16_t w[3];
  Serial.print("[scd4x] found at 0x62  ");
  if (sensirionCommand(SCD_ADDR, 0x3682) && (delay(1), sensirionRead(SCD_ADDR, w, 3))) {
    printSerialNumber(w);
  } else {
    Serial.print("serial unreadable");
  }
  if (sensirionCommand(SCD_ADDR, 0x202F) && (delay(1), sensirionRead(SCD_ADDR, w, 1))) {
    Serial.print((w[0] >> 12) == 1 ? "  variant SCD41" : "  variant SCD40");
  }
  Serial.println();

  if (!sensirionCommand(SCD_ADDR, 0x21B1)) {   // start periodic measurement
    Serial.println("[scd4x] start FAILED.");
    return false;
  }
  Serial.println("[scd4x] measuring; first reading in about 5 s.");
  return true;
}

// True when a new reading arrived.
bool scdPoll() {
  uint16_t w[3];
  if (!sensirionCommand(SCD_ADDR, 0xE4B8)) return false;   // data ready?
  delay(1);
  if (!sensirionRead(SCD_ADDR, w, 1) || (w[0] & 0x07FF) == 0) return false;
  if (!sensirionCommand(SCD_ADDR, 0xEC05)) return false;   // read measurement
  delay(1);
  if (!sensirionRead(SCD_ADDR, w, 3)) {
    Serial.println("[scd4x] CRC error on a reading.");
    return false;
  }
  co2Ppm = w[0];
  tempC = -45.0f + 175.0f * w[1] / 65535.0f;
  humidity = 100.0f * w[2] / 65535.0f;
  haveCo2 = true;
  return true;
}

// ---- SGP41 ----------------------------------------------------------------
bool sgpBegin() {
  if (!i2cPresent(SGP_ADDR)) {
    Serial.println("[sgp41] not found at 0x59 - check SDA/SCL, VIN, GND.");
    return false;
  }
  uint16_t w[3];
  Serial.print("[sgp41] found at 0x59  ");
  if (sensirionCommand(SGP_ADDR, 0x3682) && (delay(1), sensirionRead(SGP_ADDR, w, 3))) {
    printSerialNumber(w);
  } else {
    Serial.print("serial unreadable");
  }
  if (sensirionCommand(SGP_ADDR, 0x280E) && (delay(320), sensirionRead(SGP_ADDR, w, 1))) {
    Serial.printf("  self-test 0x%04X %s", w[0], (w[0] & 0x0003) == 0 ? "ok" : "FAILED");
  } else {
    Serial.print("  self-test unreadable");
  }
  Serial.println();
  Serial.printf("[sgp41] conditioning for %d s, then measuring.\n", SGP_CONDITIONING_S);
  return true;
}

// Once a second. Uses the SCD4x's temperature and humidity when it has them,
// the datasheet defaults (25 C, 50 %) when not.
void sgpTick() {
  uint16_t args[2] = {0x8000, 0x6666};
  if (haveCo2) {
    args[0] = (uint16_t)(constrain(humidity, 0.0f, 100.0f) * 65535.0f / 100.0f);
    args[1] = (uint16_t)((constrain(tempC, -45.0f, 130.0f) + 45.0f) * 65535.0f / 175.0f);
  }
  uint16_t w[2] = {0, 0};
  bool conditioning = sgpSeconds < SGP_CONDITIONING_S;
  bool ok = sensirionCommand(SGP_ADDR, conditioning ? 0x2612 : 0x2619, args, 2);
  delay(50);
  ok = ok && sensirionRead(SGP_ADDR, w, conditioning ? 1 : 2);
  sgpSeconds++;
  if (!ok) {
    Serial.println("[sgp41] read FAILED.");
    return;
  }
  rawVoc = w[0];
  vocIndex = vocAlgorithm.process(rawVoc);
  if (!conditioning) {
    rawNox = w[1];
    noxIndex = noxAlgorithm.process(rawNox);
  }
}

// ---- e-paper --------------------------------------------------------------
void drawPaper(bool full) {
  char line[40];
  if (full) {
    display.setFullWindow();
  } else {
    display.setPartialWindow(0, 0, display.width(), display.height());
  }
  unsigned long started = millis();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.drawRect(0, 0, display.width(), display.height(), GxEPD_BLACK);
    display.setTextColor(GxEPD_BLACK);

    display.setFont(&FreeMonoBold9pt7b);
    display.setCursor(8, 18);
    display.print("Creature bench");

    display.setFont(&FreeMonoBold18pt7b);
    display.setCursor(8, 54);
    if (haveCo2) {
      snprintf(line, sizeof(line), "%u ppm", co2Ppm);
    } else {
      snprintf(line, sizeof(line), scdReady ? "CO2 ..." : "no SCD4x");
    }
    display.print(line);

    display.setFont(&FreeMonoBold9pt7b);
    display.setCursor(8, 78);
    if (haveCo2) {
      snprintf(line, sizeof(line), "%.1f C  %.0f %%RH", tempC, humidity);
      display.print(line);
    }
    display.setCursor(8, 96);
    if (sgpReady) {
      snprintf(line, sizeof(line), "VOC %ld  NOx %ld", (long)vocIndex, (long)noxIndex);
    } else {
      snprintf(line, sizeof(line), "no SGP41");
    }
    display.print(line);
    display.setCursor(8, 114);
    snprintf(line, sizeof(line), "up %lu s", millis() / 1000UL);
    display.print(line);
  } while (display.nextPage());
  unsigned long took = millis() - started;

  Serial.printf("[paper] %s refresh took %lu ms", full ? "full" : "partial", took);
  if (took < 200) {
    Serial.print("  CHECK: too fast, BUSY is probably not connected");
  } else if (took > 9000) {
    Serial.print("  CHECK: timed out, look at BUSY / RST / the BS switch");
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("[air + e-paper] bench test");

  Wire.begin(I2C_SDA, I2C_SCL);
  scdReady = scdBegin();
  sgpReady = sgpBegin();

  // Waveshare boards need the short 2 ms reset pulse.
  SPI.begin(EPD_CLK, -1, EPD_DIN, EPD_CS);
  display.init(115200, true, 2, false);
  display.setRotation(1);
  drawPaper(true);

  lastTick = millis();
  lastPaper = millis();
}

void loop() {
  if (millis() - lastTick < 1000) {
    delay(5);
    return;
  }
  lastTick += 1000;

  bool fresh = scdReady && scdPoll();
  if (sgpReady) sgpTick();

  if (fresh || !scdReady) {
    if (haveCo2) {
      Serial.printf("co2 %u ppm   temp %.2f C   hum %.1f %%   ", co2Ppm, tempC, humidity);
    }
    if (sgpReady) {
      Serial.printf("voc raw %u index %ld   nox raw %u index %ld%s", rawVoc, (long)vocIndex,
                    rawNox, (long)noxIndex,
                    sgpSeconds <= SGP_CONDITIONING_S ? "   (conditioning)" : "");
    }
    Serial.println();
    if (haveCo2) {
      bool plausible = co2Ppm >= 350 && co2Ppm <= 5000 && tempC > 0 && tempC < 50;
      Serial.println(plausible ? "PASS-ish: CO2 plausible; breathe on it and watch it climb."
                               : "CHECK: CO2 or temperature implausible.");
    }
  }

  if (millis() - lastPaper >= PAPER_EVERY_MS) {
    lastPaper = millis();
    paperCount++;
    drawPaper(paperCount % PAPER_FULL_EVERY == 0);
    lastTick = millis();   // the refresh blocks; do not try to catch up
  }
}
