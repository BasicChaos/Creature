"""
Curiosity (v06.9): when nothing has surprised it for a while, it probes.

The dark-room probe in field_lab showed a looped creature staying active in an
empty room, but there the probe was a number added to its senses. This is the
same drive on the real body: a probe is something the creature does, a brief
lift in the strip's white or a short tone, and what comes back is predicted by
the forward model and felt through the loop cells like any other echo.

Boredom is measured in the field's own terms: the number of ticks since its last
significant event. It rises from zero after a quiet stretch and resets the
moment anything surprises it, including its own echo.

Two probes:

  light  a lift in white on every pixel, sized by boredom. Mostly faint, with an
         occasional larger burst, so the predictive field cannot settle into it.
  voice  a short tone, rarely, at a pitch chosen from the band of its range it
         knows least or has been most wrong about. That is how it explores its
         own voice instead of repeating one note.

The drive only proposes. The collector decides whether a probe goes out: never
while the field sleeps, never a tone while the speaker is muted or too soon
after the last one. Plain Python, seeded, no numpy.
"""

import os
import random

from mind.forward_model_v06 import SOUND_PITCH_BINS, pitch_bin_center

# Boredom starts this many ticks after the last significant event, and is full
# this many ticks after that.
BORED_AFTER_TICKS = int(os.environ.get("CREATURE_BORED_AFTER_TICKS", "120"))
BORED_RAMP_TICKS = int(os.environ.get("CREATURE_BORED_RAMP_TICKS", "300"))

# Light probe: the share of the strip's channel cap a fully bored burst adds.
LIGHT_PROBE_MAX = float(os.environ.get("CREATURE_PROBE_LIGHT_MAX", "0.35"))
LIGHT_BURST_CHANCE = 0.15     # per tick, as in the dark-room probe
LIGHT_IDLE_LEVEL = 0.1        # the faint lift between bursts

# Voice probe: sparse on purpose. In a room where nothing else happens it comes
# to roughly six tones an hour, never closer to the last tone than the gap.
VOICE_PROBE_CHANCE = float(os.environ.get("CREATURE_PROBE_VOICE_CHANCE", "0.003"))
VOICE_PROBE_GAP_TICKS = int(os.environ.get("CREATURE_PROBE_VOICE_GAP_TICKS", "120"))
VOICE_PROBE_MS = 300
VOICE_PROBE_VOLUME = 0.75


def boredom(ticks_since_event):
    """0 while something surprised it recently, rising to 1 over a quiet stretch."""
    if BORED_RAMP_TICKS <= 0:
        return 1.0 if ticks_since_event >= BORED_AFTER_TICKS else 0.0
    return max(0.0, min(1.0, (ticks_since_event - BORED_AFTER_TICKS) / BORED_RAMP_TICKS))


class Curiosity:
    """Proposes probes from boredom. Keeps only what it needs to pace itself."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.ticks_since_voice = VOICE_PROBE_GAP_TICKS
        self.light_probes = 0
        self.voice_probes = 0
        self.last = {"bored": 0.0, "light": 0.0, "voice": None}

    def pick_pitch(self, sound_model):
        """Half the time the band it has tried least, otherwise the band it has
        recently been most wrong about. A little jitter inside the band, so the
        same band is not always the same note."""
        bands = list(range(SOUND_PITCH_BINS))
        if sound_model is None:
            index = self.rng.choice(bands)
        elif self.rng.random() < 0.5:
            fewest = min(sound_model.pitch_n)
            index = self.rng.choice([i for i in bands if sound_model.pitch_n[i] == fewest])
        else:
            worst = max(sound_model.pitch_err)
            index = self.rng.choice([i for i in bands if sound_model.pitch_err[i] == worst])
        jitter = 2.0 ** (self.rng.uniform(-0.4, 0.4) / SOUND_PITCH_BINS)
        return round(pitch_bin_center(index) * jitter, 1)

    def step(self, ticks_since_event, sound_model=None, voice_allowed=True):
        """One tick. Returns {"bored", "light", "voice"}: the white lift to add
        (0-1 of LIGHT_PROBE_MAX already applied) and a tone to play or None."""
        self.ticks_since_voice += 1
        bored = boredom(ticks_since_event)

        light = 0.0
        voice = None
        if bored > 0.0:
            burst = self.rng.random() if self.rng.random() < LIGHT_BURST_CHANCE else LIGHT_IDLE_LEVEL
            light = bored * burst * LIGHT_PROBE_MAX
            if (
                voice_allowed
                and self.ticks_since_voice >= VOICE_PROBE_GAP_TICKS
                and self.rng.random() < bored * VOICE_PROBE_CHANCE
            ):
                voice = {
                    "freq": self.pick_pitch(sound_model),
                    "ms": VOICE_PROBE_MS,
                    "vol": VOICE_PROBE_VOLUME,
                }

        self.last = {"bored": round(bored, 4), "light": round(light, 4), "voice": voice}
        return self.last

    def note_voice(self):
        """Any tone went out, probe or not. The gap counts from it."""
        self.ticks_since_voice = 0

    def note_probe(self, light=False, voice=False):
        """A proposed probe was actually sent."""
        if light:
            self.light_probes += 1
        if voice:
            self.voice_probes += 1

    def snapshot(self):
        return {
            "bored": self.last["bored"],
            "light": self.last["light"],
            "voice_probes": self.voice_probes,
            "light_probes": self.light_probes,
            "ticks_since_voice": self.ticks_since_voice,
        }
