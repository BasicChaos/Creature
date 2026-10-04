"""
Check the plain-Python gas index (mind/gas_index.py) against Sensirion's own C.

The reference file holds what Sensirion's Gas Index Algorithm 3.2.0, compiled
from their C, gave for the scripted raw counts below: every 25th index of
40000 samples, for VOC and for NOx. This runs the same counts through the
Python port and compares.

    python tools/gas_index_check.py            # compare with the reference
    python tools/gas_index_check.py --input    # print the raw counts, one "voc nox" per line

Pass: no index differs by more than 1 (the C works in 32-bit floats, Python in
64-bit, so a value can land on the other side of a rounding edge).
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mind.gas_index import GasIndex

SAMPLES = 40000
EVERY = 25
SEED = 7
REFERENCE = Path(__file__).resolve().parent / "gas_index_reference.json"


def scripted_counts():
    """About eleven hours of made-up air: a slow drift, sensor noise, and events
    that pull the count down for a few minutes (VOC) or up (NOx)."""
    rng = random.Random(SEED)
    voc_base, nox_base = 29500.0, 17000.0
    voc_event = nox_event = 0.0
    rows = []
    for i in range(SAMPLES):
        voc_base += rng.gauss(0.0, 1.5)
        nox_base += rng.gauss(0.0, 0.8)
        if rng.random() < 1.0 / 1800.0:
            voc_event = rng.uniform(300.0, 6000.0)
        if rng.random() < 1.0 / 4000.0:
            nox_event = rng.uniform(500.0, 12000.0)
        voc_event *= 0.992
        nox_event *= 0.997
        voc = int(voc_base - voc_event + rng.gauss(0.0, 12.0))
        nox = int(nox_base + nox_event + rng.gauss(0.0, 8.0))
        if i % 9000 == 5000:
            voc = 0          # a failed read: the algorithm must hold its last count
        rows.append((voc, nox))
    return rows


def main():
    rows = scripted_counts()
    if "--input" in sys.argv:
        for voc, nox in rows:
            print(voc, nox)
        return 0

    reference = json.loads(REFERENCE.read_text())
    voc_algo, nox_algo = GasIndex("voc"), GasIndex("nox")
    worst = {"voc": 0, "nox": 0}
    differing = {"voc": 0, "nox": 0}
    seen = {"voc": set(), "nox": set()}
    for i, (voc, nox) in enumerate(rows):
        got = {"voc": voc_algo.process(voc), "nox": nox_algo.process(nox)}
        if i % EVERY == 0:
            for kind in ("voc", "nox"):
                want = reference[kind][i // EVERY]
                gap = abs(got[kind] - want)
                seen[kind].add(want)
                worst[kind] = max(worst[kind], gap)
                differing[kind] += gap > 0
    ok = True
    for kind in ("voc", "nox"):
        count = len(reference[kind])
        print(f"{kind}: {count} compared, reference index ran {min(seen[kind])} to "
              f"{max(seen[kind])}, {differing[kind]} differ, largest difference {worst[kind]}")
        ok = ok and worst[kind] <= 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
