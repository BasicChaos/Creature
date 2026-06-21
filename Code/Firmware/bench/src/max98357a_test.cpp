// Creature v06 bench test: MAX98357A amp + speaker (I2S out).
// Pass: a test tone plays clean; volume responds to the commanded level; no
// constant hiss or distortion at rest (silence is truly silent).
//
// Uses the legacy driver/i2s.h API on I2S peripheral 1, matching the platform
// pinned in platformio.ini (espressif32 6.x / Arduino-ESP32 2.x). The mic in the
// main firmware uses I2S peripheral 0, so the two never collide.
// Pins (WIRING v06): BCLK=15, LRC/WS=16, DIN/DOUT=17.

#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>

#define I2S_PORT     I2S_NUM_1
#define I2S_BCLK     15
#define I2S_LRC      16
#define I2S_DOUT     17
#define SAMPLE_RATE  16000

float phase = 0.0f;

void i2sSetup() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = SAMPLE_RATE,
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
    .bck_io_num = I2S_BCLK,
    .ws_io_num = I2S_LRC,
    .data_out_num = I2S_DOUT,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
  i2s_driver_install(I2S_PORT, &cfg, 0, NULL);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// Play a sine tone of the given frequency and amplitude (0..1) for ms.
void playTone(float freq, float amp, int ms) {
  const float dt = 2.0f * PI * freq / SAMPLE_RATE;
  const int total = SAMPLE_RATE * ms / 1000;
  int16_t buf[256];
  int done = 0;
  while (done < total) {
    int n = min(256, total - done);
    for (int i = 0; i < n; i++) {
      buf[i] = (int16_t)(amp * 32767.0f * sinf(phase));
      phase += dt;
      if (phase > 2.0f * PI) phase -= 2.0f * PI;
    }
    size_t written = 0;
    i2s_write(I2S_PORT, buf, n * sizeof(int16_t), &written, portMAX_DELAY);
    done += n;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  i2sSetup();
  Serial.println();
  Serial.println("[max98357a] bench test  (I2S1: BCLK=15, WS=16, DOUT=17)");
  Serial.println("expect a clean 440 Hz tone in 3 rising volume steps, then silence.");
}

void loop() {
  Serial.println("440 Hz @ 25%"); playTone(440.0f, 0.25f, 700);
  Serial.println("440 Hz @ 50%"); playTone(440.0f, 0.50f, 700);
  Serial.println("440 Hz @ 90%"); playTone(440.0f, 0.90f, 700);
  Serial.println("silence");      i2s_zero_dma_buffer(I2S_PORT); delay(1500);
  Serial.println("PASS if the tone is clean and the volume clearly steps up.");
  Serial.println();
}
