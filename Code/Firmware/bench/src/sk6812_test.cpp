// Creature v06 bench test: SK6812 RGBW strip (data on GPIO 4, ~16 px).
// Pass: a test pattern lights every pixel; all four channels (R, G, B, W) show
// correctly; no dead or wrong-colour pixels.
//
// Safety: brightness is capped at 40 and we never drive all channels white at
// once, to avoid a USB-5V brownout/reset. For full brightness add an external
// 5V supply with shared ground. Confirm the 470 ohm data resistor and the
// 1000 uF cap across 5V/GND are in place before driving the strip.
//
// Library: adafruit/Adafruit NeoPixel (set in platformio.ini env:sk6812).

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

#define STRIP_PIN 4
#define NUM_PX    16

Adafruit_NeoPixel strip(NUM_PX, STRIP_PIN, NEO_GRBW + NEO_KHZ800);

void fillAll(uint32_t color) {
  for (int i = 0; i < NUM_PX; i++) strip.setPixelColor(i, color);
  strip.show();
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  strip.begin();
  strip.setBrightness(40);   // keep low on USB 5V
  strip.clear();
  strip.show();
  Serial.println();
  Serial.println("[sk6812] bench test  (pin 4, 16 px, GRBW)");
  Serial.println("watch the strip: expect RED, GREEN, BLUE, then WHITE, then a chase.");
}

void loop() {
  Serial.println("RED");                 fillAll(strip.Color(255, 0, 0, 0)); delay(1200);
  Serial.println("GREEN");               fillAll(strip.Color(0, 255, 0, 0)); delay(1200);
  Serial.println("BLUE");                fillAll(strip.Color(0, 0, 255, 0)); delay(1200);
  Serial.println("WHITE (W channel)");   fillAll(strip.Color(0, 0, 0, 255)); delay(1200);

  Serial.println("chase (W)...");
  strip.clear();
  for (int i = 0; i < NUM_PX; i++) {
    strip.setPixelColor(i, strip.Color(0, 0, 0, 120));
    strip.show();
    delay(60);
    strip.setPixelColor(i, 0);
  }
  strip.show();
  Serial.println("PASS if every pixel showed all 4 channels, no dead/wrong pixels.");
  Serial.println();
}
