"""
Creature dashboard server.

Serves a single HTML page plus small JSON endpoints that read the same
database the collector writes to, and the creature_state.json live snapshot.

Run on the Pi:
    python dashboard/server.py

Then open from any device on the same network:
    http://<pi-ip>:8080

It uses the standard library only. No pip install needed.
"""

import json
import mimetypes
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

# Shared with the collector, so reader and writer always agree on paths.
from common.paths import DB_PATH, STATE_JSON_PATH, MUTE_FLAG_PATH

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(DASHBOARD_DIR, "index.html")
HOST = os.environ.get("CREATURE_DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.environ.get("CREATURE_DASHBOARD_PORT", "8080"))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Sensor layer: the creature's senses exposed as plain sensors for other apps.
# Bump SENSOR_SCHEMA if a field is renamed or removed (adding fields is fine).
SENSOR_SCHEMA = 1
SENSOR_STALE_SECONDS = 5.0
SENSORS = {
    "light": {"key": "light_lux", "unit": "lux", "label": "Ambient light"},
    "sound": {"key": "sound_rms", "unit": "rms", "label": "Sound level"},
    "motion": {"key": "motion", "unit": "g", "label": "Motion (IMU)"},
    "temperature": {"key": "temp_c", "unit": "C", "label": "Temperature"},
    "pressure": {"key": "pressure_hpa", "unit": "hPa", "label": "Air pressure"},
}


def open_db_readonly():
    """Open the dashboard database with a read timeout so it does not block the collector."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def read_state():
    """Latest live snapshot written by the collector each loop."""
    try:
        with open(STATE_JSON_PATH) as state_file:
            return json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return {}


def speaker_muted():
    return os.path.exists(MUTE_FLAG_PATH)


def set_speaker_muted(muted):
    """Create or remove the mute flag the collector checks each tick."""
    if muted:
        os.makedirs(os.path.dirname(MUTE_FLAG_PATH) or ".", exist_ok=True)
        with open(MUTE_FLAG_PATH, "w"):
            pass
    else:
        try:
            os.remove(MUTE_FLAG_PATH)
        except FileNotFoundError:
            pass


def read_sensors():
    """
    Raw sensor readings as a stable, app-friendly document.

    Every sensor is always present; `value` is null when the hardware has not
    reported it yet (e.g. temperature before BME280 firmware is running).
    """
    state = read_state()
    raw = dict(state.get("sensors") or {})
    # Snapshots from before the sensors block still carry BME280 values here.
    weather = state.get("weather_raw") or {}
    for key in ("temp_c", "pressure_hpa"):
        if raw.get(key) is None and weather.get(key):
            raw[key] = weather[key]

    try:
        age = max(0.0, time.time() - os.path.getmtime(STATE_JSON_PATH))
    except OSError:
        age = None
    stale = age is None or age > SENSOR_STALE_SECONDS

    sensors = {}
    for name, meta in SENSORS.items():
        value = raw.get(meta["key"])
        sensors[name] = {
            "value": value,
            "unit": meta["unit"],
            "label": meta["label"],
            "available": value is not None,
        }
    return {
        "schema": SENSOR_SCHEMA,
        "updated_at": state.get("updated_at"),
        "tick": state.get("tick"),
        "age_s": round(age, 2) if age is not None else None,
        "stale": stale,
        "sensors": sensors,
    }


def read_field_history(seconds):
    """Recent per-tick field rows for charts/scrubbing, oldest first."""
    try:
        conn = open_db_readonly()
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
            WHERE datetime(logged_at) >= datetime('now', 'localtime', ?)
            ORDER BY id ASC
            """,
            (f"-{int(seconds)} seconds",),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def read_events(limit=80):
    """Recent significant field events, newest first."""
    try:
        conn = open_db_readonly()
        rows = conn.execute(
            """
            SELECT logged_at, tick, event_type, significance, sound_norm,
                   light_norm, primary_cell, details_json
            FROM field_event_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def read_sleep_summaries(limit=40):
    """Recent sleep maintenance summaries, newest first."""
    try:
        conn = open_db_readonly()
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
            (int(limit),),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def read_health():
    """Quick database facts for the health panel (v.05 field tables)."""
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
        conn = open_db_readonly()

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console quiet

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Read-only LAN API: let browser apps on other origins call it.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Control endpoint, local dashboard only. The custom header forces a CORS
        # preflight that we never answer, so other web origins cannot call it.
        if urlparse(self.path).path != "/api/speaker" or self.headers.get("X-Creature") != "1":
            self.send_error(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            muted = payload["muted"]
            if not isinstance(muted, bool):
                raise ValueError
        except (ValueError, KeyError, json.JSONDecodeError):
            self.send_error(400, 'Expected {"muted": true|false}')
            return
        try:
            set_speaker_muted(muted)
        except OSError:
            self.send_error(500, "Could not write mute flag")
            return
        self._send_json({"muted": speaker_muted()})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            try:
                with open(HTML_PATH, "rb") as html_file:
                    body = html_file.read()
            except OSError:
                self.send_error(500, "index.html not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        filename = path.lstrip("/")
        _, ext = os.path.splitext(filename)
        if "/" not in filename and ext.lower() in IMAGE_EXTENSIONS:
            image_path = os.path.join(DASHBOARD_DIR, filename)
            try:
                with open(image_path, "rb") as image_file:
                    body = image_file.read()
            except OSError:
                self.send_error(404, "Image not found")
                return

            self.send_response(200)
            content_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # The live snapshot written by the collector each tick.
        if path == "/api/field":
            self._send_json(read_state())
            return

        if path == "/api/field_history":
            query = parse_qs(parsed.query)
            seconds = int(query.get("seconds", ["600"])[0])
            self._send_json(read_field_history(seconds))
            return

        if path == "/api/events":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["80"])[0])
            self._send_json(read_events(limit))
            return

        if path == "/api/sleep_summaries":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["40"])[0])
            self._send_json(read_sleep_summaries(limit))
            return

        # Sensor layer for other apps/services.
        if path == "/api/sensors":
            self._send_json(read_sensors())
            return

        if path.startswith("/api/sensors/"):
            name = path[len("/api/sensors/"):]
            doc = read_sensors()
            if name not in doc["sensors"]:
                self.send_response(404)
                body = json.dumps({"error": "unknown sensor", "known": list(SENSORS)}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_json({
                "schema": doc["schema"], "updated_at": doc["updated_at"],
                "stale": doc["stale"], "name": name, **doc["sensors"][name],
            })
            return

        if path == "/api/speaker":
            self._send_json({"muted": speaker_muted()})
            return

        if path == "/api/health":
            self._send_json(read_health())
            return

        self.send_error(404, "Not found")


if __name__ == "__main__":
    print(f"Creature dashboard serving on http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    print(f"Live state file: {STATE_JSON_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
