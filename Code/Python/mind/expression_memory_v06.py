"""
Expression memory (v06.5): the creature's autobiography.

The field and the decoder are stateless about the past. This module gives the
creature a lasting record of what it has expressed: a graph of its own behavior.
Each tick the decoder's signal (arousal, balance, tempo, whether it voices) is
quantized into a node, transitions between nodes are weighted, unused paths decay.
The graph persists across runs, so two creatures with different histories grow
different graphs, which is a usable identity.

This is the runtime home of the logic validated offline in
`tools/field_lab_v06.py` (the --exprmem / --exprbias / --exprnov gates). The
primitives below are the single source of truth; field_lab imports them.

Record is the only behavior wired into the live collector for v06.5: it is
passive and does not change what the body does. Bias and novelty steering (the
temperament band) are kept here as primitives but are not yet fed back into the
body, because that needs a decoder that renders from a steered signal and a
hardware loop to validate against. See `Creature Expression as Memory.md`.
"""

import json
import math
import os

from mind.expression_v06 import voice_command_from_signal


# ---------------------------------------------------------------------------
# Primitives (shared with field_lab_v06)
# ---------------------------------------------------------------------------

def clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def expression_vector(signal, speaker_activation):
    """The body's emitted state for this tick, exactly as the decoder made it:
    (arousal, balance01, tempo, voiced). balance is remapped from -1..1 to 0..1;
    voiced is 1.0 when the decoder would send a VOX tone, else 0.0."""
    a = clamp01(float(signal.get("A", 0.0) or 0.0))
    b = clamp01((float(signal.get("B", 0.0) or 0.0) + 1.0) * 0.5)
    t = clamp01(float(signal.get("T", 0.0) or 0.0))
    voiced = 1.0 if voice_command_from_signal(signal, speaker_activation) else 0.0
    return (a, b, t, voiced)


def quantize_expression(vec, bins):
    """Grid-quantize an expression vector to a node key. `bins` is the resolution
    knob: too many and every tick is a fresh node, too few and all collapses."""
    a, b, t, voiced = vec

    def q(x):
        return min(bins - 1, int(x * bins))

    return (q(a), q(b), q(t), int(round(voiced)))


def dequantize_expression(node, bins):
    """Node key back to a representative expression vector (bin centres)."""
    qa, qb, qt, qv = node
    return ((qa + 0.5) / bins, (qb + 0.5) / bins, (qt + 0.5) / bins, float(qv))


class ExpressionGraph:
    """An autobiographical graph of the creature's own expressions.

    Nodes are quantized expression states. Edges are transitions between
    consecutive states. `visits`/`count` are cumulative (the life's full tally,
    the identity). `node_weight`/`edge_weight` are the same tally under slow
    decay: the live graph, where unused paths fade and prune."""

    def __init__(self, bins=5, decay=0.999, prune=0.01):
        self.bins = int(bins)
        self.decay = float(decay)
        self.prune = float(prune)
        self.visits = {}        # node -> cumulative count
        self.node_weight = {}   # node -> decayed weight
        self.count = {}         # (src, dst) -> cumulative count
        self.edge_weight = {}   # (src, dst) -> decayed weight
        self.prev = None
        self.ticks = 0

    def observe(self, vec):
        node = quantize_expression(vec, self.bins)
        self.ticks += 1
        if self.decay < 1.0:
            for k in list(self.edge_weight):
                w = self.edge_weight[k] * self.decay
                if w < self.prune:
                    del self.edge_weight[k]
                else:
                    self.edge_weight[k] = w
            for k in list(self.node_weight):
                self.node_weight[k] *= self.decay
        self.visits[node] = self.visits.get(node, 0) + 1
        self.node_weight[node] = self.node_weight.get(node, 0.0) + 1.0
        if self.prev is not None:
            edge = (self.prev, node)
            self.count[edge] = self.count.get(edge, 0) + 1
            self.edge_weight[edge] = self.edge_weight.get(edge, 0.0) + 1.0
        self.prev = node
        return node

    def visit_entropy(self):
        """Shannon entropy of the node-visit distribution, normalized 0..1. Near
        0 is a rut (one groove), near 1 an even smear (no habit)."""
        total = sum(self.visits.values())
        if total <= 0 or len(self.visits) <= 1:
            return 0.0
        h = 0.0
        for c in self.visits.values():
            p = c / total
            h -= p * math.log(p, 2)
        return h / math.log(len(self.visits), 2)

    def top_motifs(self, k=5, include_self=True):
        """The most-travelled transitions. include_self=False drops dwell
        (X -> X) and shows the actual moves between states."""
        items = self.count.items()
        if not include_self:
            items = [(e, c) for e, c in items if e[0] != e[1]]
        return sorted(items, key=lambda kv: kv[1], reverse=True)[:k]

    def node_distribution(self):
        total = sum(self.visits.values()) or 1
        return {n: c / total for n, c in self.visits.items()}

    def edge_distribution(self):
        total = sum(self.count.values()) or 1
        return {e: c / total for e, c in self.count.items()}

    def predict_next(self, node):
        """Where the creature usually goes from `node`: the count-weighted
        centroid of its successors, dwell included. None if no recorded future."""
        succ = [(dst, c) for (src, dst), c in self.count.items() if src == node]
        if not succ:
            return None
        total = sum(c for _, c in succ)
        acc = [0.0, 0.0, 0.0, 0.0]
        for dst, c in succ:
            vec = dequantize_expression(dst, self.bins)
            for i in range(4):
                acc[i] += vec[i] * c
        return tuple(a / total for a in acc)

    # --- persistence ------------------------------------------------------

    @staticmethod
    def _nk(node):
        return "|".join(str(x) for x in node)

    @staticmethod
    def _pn(text):
        return tuple(int(x) for x in text.split("|"))

    def to_dict(self):
        nk = self._nk
        return {
            "format": "expr-graph-v1",
            "bins": self.bins,
            "decay": self.decay,
            "prune": self.prune,
            "ticks": self.ticks,
            "prev": nk(self.prev) if self.prev is not None else None,
            "visits": {nk(n): c for n, c in self.visits.items()},
            "node_weight": {nk(n): round(w, 6) for n, w in self.node_weight.items()},
            "count": {nk(s) + ">" + nk(d): c for (s, d), c in self.count.items()},
            "edge_weight": {nk(s) + ">" + nk(d): round(w, 6)
                            for (s, d), w in self.edge_weight.items()},
        }

    @classmethod
    def from_dict(cls, data):
        g = cls(bins=data.get("bins", 5), decay=data.get("decay", 0.999),
                prune=data.get("prune", 0.01))
        pn = cls._pn
        g.ticks = int(data.get("ticks", 0))
        g.prev = pn(data["prev"]) if data.get("prev") else None
        g.visits = {pn(k): int(v) for k, v in data.get("visits", {}).items()}
        g.node_weight = {pn(k): float(v) for k, v in data.get("node_weight", {}).items()}
        for k, v in data.get("count", {}).items():
            s, d = k.split(">")
            g.count[(pn(s), pn(d))] = int(v)
        for k, v in data.get("edge_weight", {}).items():
            s, d = k.split(">")
            g.edge_weight[(pn(s), pn(d))] = float(v)
        return g


def graph_distance(g1, g2):
    """Total-variation distance between two autobiographies, 0..1. 0 means
    identical histories, 1 means no shared expression. The identity metric."""

    def tv(d1, d2):
        keys = set(d1) | set(d2)
        return 0.5 * sum(abs(d1.get(k, 0.0) - d2.get(k, 0.0)) for k in keys)

    node_tv = tv(g1.node_distribution(), g2.node_distribution())
    edge_tv = tv(g1.edge_distribution(), g2.edge_distribution())
    return 0.5 * (node_tv + edge_tv)


def novelty_target(graph, vec, bins):
    """Aim at the least-explored expression state next to where bias wants to go.
    Look at the grid neighbours (one step per axis, plus a voiced flip) and pick
    the one visited least so far. Directed exploration, not a twitch."""
    node = quantize_expression(vec, bins)
    qa, qb, qt, qv = node
    candidates = [node, (qa, qb, qt, 1 - qv)]
    for i, q in enumerate((qa, qb, qt)):
        for step in (-1, 1):
            nq = q + step
            if 0 <= nq < bins:
                cand = list(node)
                cand[i] = nq
                candidates.append(tuple(cand))
    best = min(candidates, key=lambda c: (graph.visits.get(c, 0), c))
    return dequantize_expression(best, bins)


# ---------------------------------------------------------------------------
# Runtime wrapper: the live autobiography
# ---------------------------------------------------------------------------

DEFAULT_BINS = int(os.environ.get("CREATURE_EXPR_BINS", "5"))
DEFAULT_DECAY = float(os.environ.get("CREATURE_EXPR_DECAY", "0.999"))


class ExpressionMemory:
    """The live autobiography the collector keeps. Passive: it records what the
    body expressed, persists across runs, and reports stats. It does not change
    the body output."""

    def __init__(self, bins=DEFAULT_BINS, decay=DEFAULT_DECAY):
        self.graph = ExpressionGraph(bins=bins, decay=decay)

    def observe(self, signal, speaker_activation=0.0):
        """Record one tick from the decoder's expression signal."""
        vec = expression_vector(signal, speaker_activation)
        return self.graph.observe(vec)

    def stats(self):
        """Compact autobiography summary for the live snapshot / dashboard."""
        g = self.graph
        return {
            "ticks": g.ticks,
            "nodes": len(g.visits),
            "edges": len(g.count),
            "visit_entropy": round(g.visit_entropy(), 4),
            "top_motifs": [
                {"from": list(src), "to": list(dst), "count": c}
                for (src, dst), c in g.top_motifs(5, include_self=False)
            ],
        }

    def save(self, path):
        """Atomic JSON write of the autobiography. Never raises into the loop."""
        try:
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as out:
                json.dump(self.graph.to_dict(), out)
            os.replace(tmp, str(path))
            return True
        except OSError:
            return False

    def load(self, path):
        """Reload a persisted autobiography if the file exists. Returns True on
        success. A new or unreadable file leaves a fresh graph."""
        try:
            with open(str(path)) as src:
                data = json.load(src)
        except (OSError, ValueError):
            return False
        try:
            self.graph = ExpressionGraph.from_dict(data)
            return True
        except (KeyError, ValueError, TypeError):
            return False
