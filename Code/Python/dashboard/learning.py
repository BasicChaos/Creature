"""
Learning history for the dashboard: the few slow numbers that show whether the
creature is getting better at predicting, read from the collector's database.

Used by the dashboard server (`/api/learning`) and the static exporter, so both
show the same thing. Standard library only.

Four series, over a window of the creature's own ticks, cut into buckets:

  light     how much of its own light's effect the forward model accounted for,
            per bucket: 1 - sum|miss| / sum|change|, over ticks where the strip
            actually changed. This is the forward model's own "explained"
            measure, recomputed from the logged predictions. (The model also
            subtracts a slow room trend that is not logged; it is small.)
  voice     one point per tone the body reported hearing at its own pitch: how
            far the heard echo was from the predicted one, as a share of the
            larger of the two.
  surprise  average activation across the ring. In the predictive cell model a
            cell's activation is its prediction error, so this is how surprised
            the field was.
  links     the weight of each ring link.

The database is large, so every query is bounded by an id range worked out from
the logging cadence. Never scan these tables, and never ask for MIN(id) and
MAX(id) together: SQLite answers that with a full scan.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind.cell_field_v06 import CELL_COUNT
from mind.forward_model_v06 import LIGHT_ACTED_THRESHOLD

WINDOWS = {"6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}
DEFAULT_WINDOW = "24h"
BUCKETS = 96
LIGHT_MIN_ROWS = 5          # fewer acted ticks than this in a bucket says nothing

# Logging cadence, in rows per tick, with slack for restarts and gaps.
CELL_ROWS_PER_TICK = CELL_COUNT / 30.0
WEIGHT_ROWS_PER_TICK = CELL_COUNT / 300.0
SLACK = 1.5


def _open(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _max_id(conn, table):
    row = conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()
    return row[0] or 0


def _clamp(value, low, high):
    return max(low, min(high, value))


def read_learning(db_path, window=DEFAULT_WINDOW):
    window_ticks = WINDOWS.get(window, WINDOWS[DEFAULT_WINDOW])
    out = {
        "window": window if window in WINDOWS else DEFAULT_WINDOW,
        "window_ticks": window_ticks,
        "tick_from": None, "tick_to": None, "at_from": None, "at_to": None,
        "light": [], "voice": [], "surprise": [],
        "links": {"pairs": [], "points": []},
    }
    try:
        conn = _open(db_path)
    except sqlite3.Error:
        return out

    try:
        last = conn.execute(
            "SELECT tick, logged_at FROM cell_log ORDER BY id DESC LIMIT 1").fetchone()
        if not last:
            return out
        t1 = last[0]
        t0 = max(0, t1 - window_ticks)
        bucket = max(1, -(-window_ticks // BUCKETS))
        out["tick_from"], out["tick_to"], out["at_to"] = t0, t1, last[1]

        def mid(k):
            return min(t1, t0 + int((k + 0.5) * bucket))

        # Surprise: average ring activation per bucket.
        lo = _max_id(conn, "cell_log") - int(window_ticks * CELL_ROWS_PER_TICK * SLACK) - 100
        rows = conn.execute(
            """
            SELECT (tick - ?) / ? AS k, MIN(logged_at), AVG(activation)
            FROM cell_log
            WHERE id > ? AND tick > ? AND tick <= ? AND cell < ?
            GROUP BY k ORDER BY k
            """, (t0, bucket, lo, t0, t1, CELL_COUNT)).fetchall()
        out["surprise"] = [
            {"tick": mid(k), "at": at, "value": round(v, 4)}
            for k, at, v in rows if v is not None
        ]
        if rows:
            out["at_from"] = rows[0][1]

        # Ring links: the last logged weight of each link in each bucket.
        lo = _max_id(conn, "weight_log") - int(window_ticks * WEIGHT_ROWS_PER_TICK * SLACK) - 100
        rows = conn.execute(
            """
            SELECT tick, logged_at, cell_a, cell_b, weight
            FROM weight_log
            WHERE id > ? AND tick > ? AND tick <= ? AND cell_a < ? AND cell_b < ?
            ORDER BY id
            """, (lo, t0, t1, CELL_COUNT, CELL_COUNT)).fetchall()
        # Ring-adjacent pairs only: older fields in the same table had other links.
        rows = [r for r in rows if r[3] - r[2] in (1, CELL_COUNT - 1)]
        pairs = sorted({(a, b) for _t, _at, a, b, _w in rows})
        index = {pair: i for i, pair in enumerate(pairs)}
        points = {}
        for tick, at, a, b, w in rows:
            k = (tick - t0) // bucket
            point = points.setdefault(k, {"tick": tick, "at": at, "w": [None] * len(pairs)})
            point["tick"], point["at"] = tick, at
            point["w"][index[(a, b)]] = round(w, 4) if w is not None else None
        out["links"] = {
            "pairs": [list(pair) for pair in pairs],
            "points": [points[k] for k in sorted(points)],
        }

        # The loop record has one row per tick.
        lo = _max_id(conn, "loop_log") - int(window_ticks * SLACK) - 100
        rows = conn.execute(
            """
            SELECT (tick - ?) / ? AS k, MIN(logged_at),
                   SUM(ABS(lux_err)), SUM(ABS(lux_delta)), COUNT(*)
            FROM (
                SELECT tick, logged_at, lux_err, lux_delta,
                       (ABS(out_r - LAG(out_r) OVER w) + ABS(out_g - LAG(out_g) OVER w) +
                        ABS(out_b - LAG(out_b) OVER w) + ABS(out_w - LAG(out_w) OVER w)) / 4.0 AS du
                FROM loop_log
                WHERE id > ?
                WINDOW w AS (ORDER BY id)
            )
            WHERE du >= ? AND lux_err IS NOT NULL AND lux_delta IS NOT NULL
                  AND tick > ? AND tick <= ?
            GROUP BY k ORDER BY k
            """, (t0, bucket, lo, LIGHT_ACTED_THRESHOLD, t0, t1)).fetchall()
        for k, at, err, null, n in rows:
            if n < LIGHT_MIN_ROWS or not null:
                continue
            out["light"].append({
                "tick": mid(k), "at": at, "n": n,
                "explained": round(_clamp(1.0 - err / null, -1.0, 1.0), 4),
            })

        rows = conn.execute(
            """
            SELECT tick, logged_at, vox_freq, rms_excess, rms_pred, rms_err
            FROM loop_log
            WHERE id > ? AND vox_freq IS NOT NULL AND vox_heard IS NOT NULL AND rms_err IS NOT NULL
                  AND tick > ? AND tick <= ?
            ORDER BY id
            """, (lo, t0, t1)).fetchall()
        for tick, at, freq, heard, pred, err in rows:
            size = max(abs(heard or 0.0), abs(pred or 0.0), 1e-6)
            out["voice"].append({
                "tick": tick, "at": at, "freq": round(freq, 1),
                "heard": heard, "pred": pred,
                "miss": round(min(1.0, abs(err) / size), 4),
            })
    except sqlite3.Error as error:
        out["error"] = str(error)
    finally:
        conn.close()
    return out


if __name__ == "__main__":
    import json
    import time
    from common.paths import DB_PATH

    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WINDOW
    started = time.time()
    doc = read_learning(DB_PATH, name)
    print(json.dumps({
        "window": doc["window"], "ticks": [doc["tick_from"], doc["tick_to"]],
        "seconds": round(time.time() - started, 2),
        "light": len(doc["light"]), "voice": len(doc["voice"]),
        "surprise": len(doc["surprise"]), "link_points": len(doc["links"]["points"]),
        "links": doc["links"]["pairs"], "error": doc.get("error"),
        "light_tail": doc["light"][-3:], "voice_tail": doc["voice"][-2:],
        "surprise_tail": doc["surprise"][-2:],
        "links_tail": doc["links"]["points"][-1:],
    }, indent=1))
