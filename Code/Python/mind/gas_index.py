"""
Gas index: turns the SGP41's raw VOC and NOx counts into Sensirion's index.

The raw count is a resistance, not a concentration, and it falls when there is
more gas. The index learns what is normal for the room over hours and reports
each reading against that:

  VOC index  1 to 500. 100 is the room's own average. Above about 150 something
             happened (cooking, cleaning, breath, solvent). Below 100 is cleaner
             than usual.
  NOx index  1 to 500. 1 is normal. Anything climbing above it is a
             combustion-type event (gas hob, candle, traffic air).

The index reads 0 for the first 45 samples (the sensor's blackout). NOx stays at
1 until its first real sample. Feed it one sample a second.

This is for the dashboard only. The field does not read it.

It is a plain-Python port of Sensirion's Gas Index Algorithm 3.2.0
(sensirion_gas_index_algorithm.c), kept line for line so it can be checked
against the original. The original works in 32-bit floats and this works in
Python floats, so an index can differ by one at a rounding edge. The original's
licence asks for this notice to be kept:

  Copyright (c) 2022, Sensirion AG
  All rights reserved.

  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions are met:

  * Redistributions of source code must retain the above copyright notice, this
    list of conditions and the following disclaimer.

  * Redistributions in binary form must reproduce the above copyright notice,
    this list of conditions and the following disclaimer in the documentation
    and/or other materials provided with the distribution.

  * Neither the name of Sensirion AG nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
  POSSIBILITY OF SUCH DAMAGE.
"""

import math

SAMPLING_INTERVAL = 1.0
INITIAL_BLACKOUT = 45.0
INDEX_GAIN = 230.0
SRAW_STD_INITIAL = 50.0
SRAW_STD_BONUS_VOC = 220.0
SRAW_STD_NOX = 2000.0
TAU_MEAN_HOURS = 12.0
TAU_VARIANCE_HOURS = 12.0
TAU_INITIAL_MEAN_VOC = 20.0
TAU_INITIAL_MEAN_NOX = 1200.0
INIT_DURATION_MEAN_VOC = 3600.0 * 0.75
INIT_DURATION_MEAN_NOX = 3600.0 * 4.75
INIT_TRANSITION_MEAN = 0.01
TAU_INITIAL_VARIANCE = 2500.0
INIT_DURATION_VARIANCE_VOC = 3600.0 * 1.45
INIT_DURATION_VARIANCE_NOX = 3600.0 * 5.70
INIT_TRANSITION_VARIANCE = 0.01
GATING_THRESHOLD_VOC = 340.0
GATING_THRESHOLD_NOX = 30.0
GATING_THRESHOLD_INITIAL = 510.0
GATING_THRESHOLD_TRANSITION = 0.09
GATING_VOC_MAX_DURATION_MINUTES = 60.0 * 3.0
GATING_NOX_MAX_DURATION_MINUTES = 60.0 * 12.0
GATING_MAX_RATIO = 0.3
SIGMOID_L = 500.0
SIGMOID_K_VOC = -0.0065
SIGMOID_X0_VOC = 213.0
SIGMOID_K_NOX = -0.0101
SIGMOID_X0_NOX = 614.0
VOC_INDEX_OFFSET_DEFAULT = 100.0
NOX_INDEX_OFFSET_DEFAULT = 1.0
LP_TAU_FAST = 20.0
LP_TAU_SLOW = 500.0
LP_ALPHA = -0.2
VOC_SRAW_MINIMUM = 20000
NOX_SRAW_MINIMUM = 10000
GAMMA_SCALING = 64.0
ADDITIONAL_GAMMA_MEAN_SCALING = 8.0
FIX16_MAX = 32767.0

# What changes as samples arrive. This is all that is saved and restored.
STATE_FIELDS = (
    "uptime", "sraw", "gas_index",
    "mve_initialized", "mve_mean", "mve_sraw_offset", "mve_std",
    "mve_gamma_mean_now", "mve_gamma_variance_now",
    "mve_uptime_gamma", "mve_uptime_gating", "mve_gating_duration_minutes",
    "lp_initialized", "lp_x1", "lp_x2", "lp_x3",
)


def _sigmoid(k, x0, sample):
    x = k * (sample - x0)
    if x < -50.0:
        return 1.0
    if x > 50.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(x))


class GasIndex:
    def __init__(self, kind):
        """kind: "voc" or "nox"."""
        if kind not in ("voc", "nox"):
            raise ValueError("kind must be 'voc' or 'nox'")
        self.kind = kind
        nox = kind == "nox"
        self.index_offset = NOX_INDEX_OFFSET_DEFAULT if nox else VOC_INDEX_OFFSET_DEFAULT
        self.sraw_minimum = NOX_SRAW_MINIMUM if nox else VOC_SRAW_MINIMUM
        self.gating_max_duration_minutes = (
            GATING_NOX_MAX_DURATION_MINUTES if nox else GATING_VOC_MAX_DURATION_MINUTES)
        self.init_duration_mean = INIT_DURATION_MEAN_NOX if nox else INIT_DURATION_MEAN_VOC
        self.init_duration_variance = (
            INIT_DURATION_VARIANCE_NOX if nox else INIT_DURATION_VARIANCE_VOC)
        self.gating_threshold = GATING_THRESHOLD_NOX if nox else GATING_THRESHOLD_VOC
        self.sigmoid_k = SIGMOID_K_NOX if nox else SIGMOID_K_VOC
        self.sigmoid_x0 = SIGMOID_X0_NOX if nox else SIGMOID_X0_VOC
        self.sigmoid_offset_default = self.index_offset

        # Learning rates of the mean and variance estimator: slow ones for the
        # long run, fast ones for the first hours.
        hours = SAMPLING_INTERVAL / 3600.0
        self.gamma_mean = (ADDITIONAL_GAMMA_MEAN_SCALING * GAMMA_SCALING * hours) / (TAU_MEAN_HOURS + hours)
        self.gamma_variance = (GAMMA_SCALING * hours) / (TAU_VARIANCE_HOURS + hours)
        tau_initial_mean = TAU_INITIAL_MEAN_NOX if nox else TAU_INITIAL_MEAN_VOC
        self.gamma_initial_mean = (
            (ADDITIONAL_GAMMA_MEAN_SCALING * GAMMA_SCALING * SAMPLING_INTERVAL)
            / (tau_initial_mean + SAMPLING_INTERVAL))
        self.gamma_initial_variance = (
            (GAMMA_SCALING * SAMPLING_INTERVAL) / (TAU_INITIAL_VARIANCE + SAMPLING_INTERVAL))
        self.lp_a1 = SAMPLING_INTERVAL / (LP_TAU_FAST + SAMPLING_INTERVAL)
        self.lp_a2 = SAMPLING_INTERVAL / (LP_TAU_SLOW + SAMPLING_INTERVAL)

        self.uptime = 0.0
        self.sraw = 0.0
        self.gas_index = 0.0
        self.mve_initialized = False
        self.mve_mean = 0.0
        self.mve_sraw_offset = 0.0
        self.mve_std = SRAW_STD_INITIAL
        self.mve_gamma_mean_now = 0.0
        self.mve_gamma_variance_now = 0.0
        self.mve_uptime_gamma = 0.0
        self.mve_uptime_gating = 0.0
        self.mve_gating_duration_minutes = 0.0
        self.lp_initialized = False
        self.lp_x1 = 0.0
        self.lp_x2 = 0.0
        self.lp_x3 = 0.0

    # ---- saving and restoring ----------------------------------------------
    def snapshot(self):
        return {name: getattr(self, name) for name in STATE_FIELDS}

    def restore(self, saved):
        """Take back a snapshot(). Returns False, and changes nothing, if it is
        not a complete one."""
        try:
            values = {}
            for name in STATE_FIELDS:
                value = saved[name]
                if name.endswith("initialized"):
                    values[name] = bool(value)
                else:
                    value = float(value)
                    if not math.isfinite(value):
                        return False
                    values[name] = value
        except (KeyError, TypeError, ValueError):
            return False
        for name, value in values.items():
            setattr(self, name, value)
        return True

    # ---- one sample ---------------------------------------------------------
    def process(self, sraw):
        """One raw count in, the index out (an integer; 0 during the blackout)."""
        sraw = int(sraw)
        if self.uptime <= INITIAL_BLACKOUT:
            self.uptime += SAMPLING_INTERVAL
        else:
            if 0 < sraw < 65000:
                if sraw < self.sraw_minimum + 1:
                    sraw = self.sraw_minimum + 1
                elif sraw > self.sraw_minimum + 32767:
                    sraw = self.sraw_minimum + 32767
                self.sraw = float(sraw - self.sraw_minimum)
            if self.kind == "voc" or self.mve_initialized:
                self.gas_index = self._mox_model(self.sraw)
                self.gas_index = self._sigmoid_scaled(self.gas_index)
            else:
                self.gas_index = self.index_offset
            self.gas_index = self._adaptive_lowpass(self.gas_index)
            if self.gas_index < 0.5:
                self.gas_index = 0.5
            if self.sraw > 0.0:
                self._estimate(self.sraw)
        return int(self.gas_index + 0.5)

    def _mean(self):
        return self.mve_mean + self.mve_sraw_offset

    def _mox_model(self, sraw):
        if self.kind == "nox":
            return ((sraw - self._mean()) / SRAW_STD_NOX) * INDEX_GAIN
        return ((sraw - self._mean()) / (-1.0 * (self.mve_std + SRAW_STD_BONUS_VOC))) * INDEX_GAIN

    def _sigmoid_scaled(self, sample):
        x = self.sigmoid_k * (sample - self.sigmoid_x0)
        if x < -50.0:
            return SIGMOID_L
        if x > 50.0:
            return 0.0
        if sample >= 0.0:
            if self.sigmoid_offset_default == 1.0:
                shift = (500.0 / 499.0) * (1.0 - self.index_offset)
            else:
                shift = (SIGMOID_L - (5.0 * self.index_offset)) / 4.0
            return ((SIGMOID_L + shift) / (1.0 + math.exp(x))) - shift
        return (self.index_offset / self.sigmoid_offset_default) * (SIGMOID_L / (1.0 + math.exp(x)))

    def _adaptive_lowpass(self, sample):
        if not self.lp_initialized:
            self.lp_x1 = sample
            self.lp_x2 = sample
            self.lp_x3 = sample
            self.lp_initialized = True
        self.lp_x1 = (1.0 - self.lp_a1) * self.lp_x1 + self.lp_a1 * sample
        self.lp_x2 = (1.0 - self.lp_a2) * self.lp_x2 + self.lp_a2 * sample
        abs_delta = abs(self.lp_x1 - self.lp_x2)
        f1 = math.exp(LP_ALPHA * abs_delta)
        tau_a = (LP_TAU_SLOW - LP_TAU_FAST) * f1 + LP_TAU_FAST
        a3 = SAMPLING_INTERVAL / (SAMPLING_INTERVAL + tau_a)
        self.lp_x3 = (1.0 - a3) * self.lp_x3 + a3 * sample
        return self.lp_x3

    def _calculate_gamma(self):
        uptime_limit = FIX16_MAX - SAMPLING_INTERVAL
        if self.mve_uptime_gamma < uptime_limit:
            self.mve_uptime_gamma += SAMPLING_INTERVAL
        if self.mve_uptime_gating < uptime_limit:
            self.mve_uptime_gating += SAMPLING_INTERVAL

        sigmoid_gamma_mean = _sigmoid(INIT_TRANSITION_MEAN, self.init_duration_mean,
                                      self.mve_uptime_gamma)
        gamma_mean = self.gamma_mean + (self.gamma_initial_mean - self.gamma_mean) * sigmoid_gamma_mean
        gating_threshold_mean = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * _sigmoid(INIT_TRANSITION_MEAN, self.init_duration_mean, self.mve_uptime_gating))
        sigmoid_gating_mean = _sigmoid(GATING_THRESHOLD_TRANSITION, gating_threshold_mean,
                                       self.gas_index)
        self.mve_gamma_mean_now = sigmoid_gating_mean * gamma_mean

        sigmoid_gamma_variance = _sigmoid(INIT_TRANSITION_VARIANCE, self.init_duration_variance,
                                          self.mve_uptime_gamma)
        gamma_variance = self.gamma_variance + (
            (self.gamma_initial_variance - self.gamma_variance)
            * (sigmoid_gamma_variance - sigmoid_gamma_mean))
        gating_threshold_variance = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * _sigmoid(INIT_TRANSITION_VARIANCE, self.init_duration_variance,
                       self.mve_uptime_gating))
        sigmoid_gating_variance = _sigmoid(GATING_THRESHOLD_TRANSITION, gating_threshold_variance,
                                           self.gas_index)
        self.mve_gamma_variance_now = sigmoid_gating_variance * gamma_variance

        self.mve_gating_duration_minutes += (SAMPLING_INTERVAL / 60.0) * (
            ((1.0 - sigmoid_gating_mean) * (1.0 + GATING_MAX_RATIO)) - GATING_MAX_RATIO)
        if self.mve_gating_duration_minutes < 0.0:
            self.mve_gating_duration_minutes = 0.0
        if self.mve_gating_duration_minutes > self.gating_max_duration_minutes:
            self.mve_uptime_gating = 0.0

    def _estimate(self, sraw):
        """Update the running mean and spread of the raw count: the room's normal."""
        if not self.mve_initialized:
            self.mve_initialized = True
            self.mve_sraw_offset = sraw
            self.mve_mean = 0.0
            return
        if self.mve_mean >= 100.0 or self.mve_mean <= -100.0:
            self.mve_sraw_offset += self.mve_mean
            self.mve_mean = 0.0
        sraw = sraw - self.mve_sraw_offset
        self._calculate_gamma()
        delta_sgp = (sraw - self.mve_mean) / GAMMA_SCALING
        c = self.mve_std - delta_sgp if delta_sgp < 0.0 else self.mve_std + delta_sgp
        additional_scaling = 1.0
        if c > 1440.0:
            additional_scaling = (c / 1440.0) * (c / 1440.0)
        self.mve_std = (
            math.sqrt(additional_scaling * (GAMMA_SCALING - self.mve_gamma_variance_now))
            * math.sqrt(
                (self.mve_std * (self.mve_std / (GAMMA_SCALING * additional_scaling)))
                + (((self.mve_gamma_variance_now * delta_sgp) / additional_scaling) * delta_sgp)))
        self.mve_mean += (self.mve_gamma_mean_now * delta_sgp) / ADDITIONAL_GAMMA_MEAN_SCALING
