import json
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

# Shared with the collector and dashboard server; see common/paths.py.
from common.paths import DB_PATH, STATE_JSON_PATH

DASHBOARD_DIR = Path(__file__).resolve().parent
EXPORT_DIR = DASHBOARD_DIR / "public"
INDEX_PATH = DASHBOARD_DIR / "index.html"

EXPORT_DIR.mkdir(exist_ok=True)

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def read_history(seconds=900):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(field_tick_log)")}
        sound_linear_expr = "sound_linear" if "sound_linear" in columns else "sound_norm AS sound_linear"
        rows = conn.execute(
            f"""
            SELECT logged_at, tick, sound_norm, {sound_linear_expr}, light_norm,
                   emitter_activation, sent_brightness,
                   energy_reserve, energy_avg, fatigue_avg,
                   memory_pressure, sleep_mode,
                   active_cells, resting_cells, dormant_cells, deep_sleep_cells,
                   live_connections, pruned_connections, events_count
            FROM field_tick_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (seconds,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in reversed(rows)]
    except sqlite3.Error:
        return []

def read_events(limit=80):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT logged_at, tick, event_type, significance, sound_norm,
                   light_norm, primary_cell, details_json
            FROM field_event_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []

def read_sleep_summaries(limit=40):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT logged_at, start_tick, end_tick, duration_ticks, reason,
                   events_reviewed, links_reinforced, links_pruned,
                   energy_before, energy_after, memory_pressure_before,
                   memory_pressure_after, details_json
            FROM sleep_summary_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []

def read_health():
    info = {
        "db_path": DB_PATH,
        "rows_field_tick": None,
        "rows_cell": None,
        "rows_weight": None,
        "rows_event": None,
        "rows_sleep": None,
        "latest_tick": None,
    }
    try:
        conn = sqlite3.connect(DB_PATH)
        def row_estimate(table):
            try:
                row = conn.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = ?",
                    (table,),
                ).fetchone()
                if row and row[0] is not None:
                    return row[0]
                return conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                return None
        info["rows_field_tick"] = row_estimate("field_tick_log")
        info["rows_cell"] = row_estimate("cell_log")
        info["rows_weight"] = row_estimate("weight_log")
        info["rows_event"] = row_estimate("field_event_log")
        info["rows_sleep"] = row_estimate("sleep_summary_log")
        try:
            info["latest_tick"] = conn.execute(
                "SELECT MAX(tick) FROM field_tick_log"
            ).fetchone()[0]
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error:
        pass
    return info

def write_json(filename, payload):
    with open(EXPORT_DIR / filename, "w") as f:
        json.dump(payload, f)

shutil.copyfile(INDEX_PATH, EXPORT_DIR / "index.html")

HARDWARE_PHOTO_PATH = DASHBOARD_DIR / "creature-v05-2.jpg"
if HARDWARE_PHOTO_PATH.exists():
    shutil.copyfile(HARDWARE_PHOTO_PATH, EXPORT_DIR / "creature-v05-2.jpg")

write_json("state.json", read_json(STATE_JSON_PATH))
write_json("history.json", read_history())
write_json("events.json", read_events())
write_json("sleep_summaries.json", read_sleep_summaries())
write_json("health.json", read_health())

print(f"Exported dashboard to {EXPORT_DIR}")
