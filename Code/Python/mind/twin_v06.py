"""
The newborn twin (after v06.9).

A second field that lives beside the real one. On every tick it gets the same
four senses and nothing else: no loop, no body. It was born later, so the gap
between the two is what the real field's longer life is worth. The history gate
in tools/field_lab_v06.py measures that gap offline on recorded senses; the twin
shows it live.

The twin must not change the Creature. The field draws its noise from the shared
`random` stream, so the twin keeps a stream of its own and swaps it in for the
length of its step. Both fields are read here through decoders held to the fixed
expression model: the relative one reads each field against its own usual, which
would hide the difference being measured.
"""

import random

from mind.cell_field_v06 import build_field, load_field, save_field
from mind.expression_v06 import ExpressionDecoderV06

GAP_SMOOTH_TICKS = 3600     # the snapshot's gaps are smoothed over about an hour


def _decoder():
    # One pixel: only A, B and T are wanted, and they do not depend on the strip.
    return ExpressionDecoderV06(pixels=1, knobs={"EXPRESSION_MODEL": "fixed"})


class Twin:
    """A newborn field stepped beside the real one, and the gaps between them."""

    def __init__(self, seed=None):
        self.field = build_field()
        self.decoder = _decoder()    # reads the twin
        self.mirror = _decoder()     # reads the real field, the same way
        # The twin's own random stream. `seed` is for the gates; live it is left
        # to chance, as the real field's is.
        self._stream = random.Random(seed).getstate()
        self.gap = {"a": 0.0, "b": 0.0, "t": 0.0, "link": 0.0}    # this tick
        self.smooth = dict(self.gap)                               # over about an hour
        self.smoothed_ticks = 0
        self._sum = {"a": 0.0, "b": 0.0, "t": 0.0}                 # since the last log row
        self._count = 0

    @property
    def age(self):
        """Ticks the twin has lived."""
        return self.field.tick_count

    def load(self, path):
        """Load the twin's saved field. False, and a newborn, if there is none."""
        return load_field(self.field, path) is not None

    def save(self, path):
        save_field(self.field, path)

    def step(self, senses, real_state):
        """One tick: the same senses as the real field, then the gaps against
        `real_state`, the state the real field returned for this tick."""
        outer = random.getstate()
        random.setstate(self._stream)
        try:
            state = self.field.step(dict(senses))
        finally:
            self._stream = random.getstate()
            random.setstate(outer)

        own = self.decoder.read(state)
        real = self.mirror.read(real_state)
        real_weights = {(c["a"], c["b"]): c["weight"] for c in real_state.get("connections") or []}
        self.gap = {
            "a": abs(real["A"] - own["A"]),
            "b": abs(real["B"] - own["B"]),
            "t": abs(real["T"] - own["T"]),
            "link": max((abs(real_weights[key] - w)
                         for key, w in self.field.weights.items() if key in real_weights),
                        default=0.0),
        }
        # A plain mean until there is an hour of it, then a running one.
        self.smoothed_ticks = min(GAP_SMOOTH_TICKS, self.smoothed_ticks + 1)
        for name, value in self.gap.items():
            self.smooth[name] += (value - self.smooth[name]) / self.smoothed_ticks
        for name in self._sum:
            self._sum[name] += self.gap[name]
        self._count += 1
        return state

    def log_row(self):
        """The mean gaps since the last call and the link gap now, then start the
        next stretch. Averaging these rows over an hour gives the history gate's
        hourly figures."""
        n = max(1, self._count)
        row = {
            "twin_age": self.age,
            "gap_a": round(self._sum["a"] / n, 5),
            "gap_b": round(self._sum["b"] / n, 5),
            "gap_t": round(self._sum["t"] / n, 5),
            "gap_link": round(self.gap["link"], 5),
        }
        self._sum = {name: 0.0 for name in self._sum}
        self._count = 0
        return row

    def snapshot(self):
        """The `twin` block of the live snapshot."""
        return {
            "age": self.age,
            "weights": [
                {"a": i, "b": j, "weight": round(w, 6)}
                for (i, j), w in sorted(self.field.weights.items())
            ],
            "gap": {
                "arousal": round(self.smooth["a"], 4),
                "balance": round(self.smooth["b"], 4),
                "tempo": round(self.smooth["t"], 4),
                "links": round(self.smooth["link"], 4),
            },
            "gap_ticks": self.smoothed_ticks,    # ticks behind the smoothing, an hour at most
        }
