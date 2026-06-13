"""
Rolling normalizer.

Turns a raw, drifting sensor stream into a 0-1 value the cell field can use.
Two parts:

  1. EMA smoothing  - an exponential moving average tames spikes. A smaller
     alpha means heavier smoothing (use it for the spiky mic).
  2. Rolling min/max - the recent operating range, so the value adapts to the
     room instead of needing fixed limits. Light that sits flat reads near 0;
     when it actually changes, the range opens and the change shows.

A minimum-range guard stops a flat, noisy signal from being stretched to fill
0-1. Until the signal really moves, it reads 0.

No serial or field code here, so this is easy to test on its own.
"""

from collections import deque


def clamp(value, low, high):
    return max(low, min(high, value))


class RollingNormalizer:
    def __init__(self, window_seconds, ema_alpha, min_range):
        """
        window_seconds: how far back the min/max range looks.
        ema_alpha:      smoothing, 0-1. Smaller = smoother. ~0.05 spiky, ~0.3 calm.
        min_range:      smallest raw span (max-min) treated as real signal.
        """
        self.window_seconds = window_seconds
        self.ema_alpha = ema_alpha
        self.min_range = min_range
        self.samples = deque()   # (timestamp, raw_value)
        self.ema = None

    def add(self, value, now):
        """Feed one raw reading. Call this as fast as samples arrive."""
        if self.ema is None:
            self.ema = value
        else:
            self.ema += self.ema_alpha * (value - self.ema)

        self.samples.append((now, value))
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def normalized(self):
        """Current smoothed value mapped into the recent range, 0-1."""
        if self.ema is None or not self.samples:
            return 0.0

        low = min(v for _, v in self.samples)
        high = max(v for _, v in self.samples)
        span = high - low

        if span < self.min_range:
            return 0.0

        return clamp((self.ema - low) / span, 0.0, 1.0)

    def debug(self):
        """Raw internals, handy for logging and tuning."""
        if not self.samples:
            return {"ema": self.ema, "min": None, "max": None, "count": 0}
        low = min(v for _, v in self.samples)
        high = max(v for _, v in self.samples)
        return {
            "ema": round(self.ema, 2) if self.ema is not None else None,
            "min": round(low, 2),
            "max": round(high, 2),
            "count": len(self.samples),
        }


if __name__ == "__main__":
    # Quick check: steady light reads ~0, a step change reads high; a sound
    # spike is smoothed down.
    t = 0.0

    light = RollingNormalizer(window_seconds=120, ema_alpha=0.2, min_range=50)
    for _ in range(200):
        light.add(2697 + (t % 2), t)   # tiny jitter, no real change
        t += 0.1
    print("steady light ->", round(light.normalized(), 3), light.debug())

    for _ in range(200):
        light.add(1230, t)             # someone covers the sensor
        t += 0.1
    print("covered light ->", round(light.normalized(), 3), light.debug())

    sound = RollingNormalizer(window_seconds=20, ema_alpha=0.05, min_range=500)
    base = [4000, 5000, 4200, 15000, 4300, 4800, 3900, 14000, 4100, 5200]
    for _ in range(50):
        for v in base:
            sound.add(v, t)
            t += 0.1
    print("noisy sound  ->", round(sound.normalized(), 3), sound.debug())
