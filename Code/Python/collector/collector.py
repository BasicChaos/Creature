"""
Creature collector (v05.3).

Reads the ESP stream (one JSON line per sample, ~10 Hz), normalizes
light and sound to 0-1, and steps the 111-cell field once per second. C29's
activation drives the onboard LED. The database is observation, not memory:
high-volume cell detail is sampled, while structure and sleep summaries are
kept as the longer-lived record.

Run on the Pi:
    cd ~/Creature/Code/Python
    source .venv/bin/activate
    python collector/collector.py /dev/ttyUSB0
    python collector/collector.py tcp://creature-esp.local:7777

ESP stream expected (raw values, normalization happens here):
    {"time_ms":123456,"light_lux":1230.8,"sound_rms":4268.8}
Lines with a "system" key, or without both sensor fields, are ignored.
"""

import atexit
import json
import os
import signal
import socket
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic, sleep

PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind.cell_field import (
    build_field,
    save_field,
    load_field,
    CELL_COUNT,
    EMITTER_ANCHOR,
    FIELD_VERSION,
)
from mind.normalize import RollingNormalizer

# --- Serial ---
BAUD = 115200
DEFAULT_PORT = "/dev/ttyUSB0"

# --- WiFi TCP ---
DEFAULT_TCP_PORT = 7777
TCP_CONNECT_TIMEOUT_SECONDS = 5.0
TCP_READ_TIMEOUT_SECONDS = 0.2
TCP_RECONNECT_SECONDS = 2.0

# --- Field tick ---
TICK_SECONDS = 1.0          # the field steps once per second (decay/memory assume this)
SAVE_EVERY_TICKS = 100      # persist field state to disk every N ticks
CELL_LOG_EVERY_TICKS = int(os.environ.get("CREATURE_CELL_LOG_EVERY_TICKS", "30"))
WEIGHT_LOG_EVERY_TICKS = int(os.environ.get("CREATURE_WEIGHT_LOG_EVERY_TICKS", "300"))
STATUS_PRINT_EVERY_TICKS = 30  # print a short live status line every N ticks
COMMIT_EVERY_TICKS = 10     # batch SQLite commits: one fsync per N ticks, not per tick
RETENTION_EVERY_TICKS = 3600
RETENTION_ENABLED = os.environ.get("CREATURE_RETENTION_ENABLED", "0") == "1"
TEMPORARY_RETENTION_HOURS = int(os.environ.get("CREATURE_TEMPORARY_RETENTION_HOURS", "12"))
EVENT_RETENTION_DAYS = int(os.environ.get("CREATURE_EVENT_RETENTION_DAYS", "21"))

# --- Normalization (tune live on the Pi while watching the dashboard) ---
# Light is slow and steady, so a short window and light smoothing.
LIGHT_WINDOW_SECONDS = 120.0
LIGHT_EMA_ALPHA = 0.2
LIGHT_MIN_RANGE = 50.0       # lux span below this counts as "no real change"
# Sound is spiky, so a heavier smooth (smaller alpha) and a shorter range window.
SOUND_WINDOW_SECONDS = 20.0
SOUND_EMA_ALPHA = 0.05
SOUND_MIN_RANGE = 500.0      # rms span below this counts as quiet
# Noise gate. The rolling normalizer reports "where is this inside the recent
# range", so in a quiet room it stretches fridge/HVAC hum to fill 0-1 and a
# silent 3 a.m. read came out near 0.13 linear, which the old floor of 0.03
# then passed and the curve boosted to ~0.27. The creature "heard" a quiet dark
# room as moderately loud all night, which kept cells firing, drained the
# energy reserve, and eroded structure. Raising the floor to 0.20 gates the
# ambient median to 0 (89% of overnight ticks read silent in replay) while
# louder transients still pass (evenings still average ~0.06). Gain and
# exponent are unchanged so real events stay reactive. NOTE: this threshold is
# on the floating normalized value, tuned to the current room. The durable fix
# is to also log raw sound_rms so the normalizer can be calibrated against an
# absolute level; see the design doc's open items.
SOUND_RESPONSE_FLOOR = float(os.environ.get("CREATURE_SOUND_RESPONSE_FLOOR", "0.20"))
SOUND_RESPONSE_GAIN = float(os.environ.get("CREATURE_SOUND_RESPONSE_GAIN", "1.25"))
SOUND_RESPONSE_EXPONENT = float(os.environ.get("CREATURE_SOUND_RESPONSE_EXPONENT", "0.65"))

# --- LED ---
DEFAULT_LED_MAX_BRIGHTNESS = 80
LED_MAX_BRIGHTNESS = int(os.environ.get("CREATURE_LED_MAX_BRIGHTNESS", DEFAULT_LED_MAX_BRIGHTNESS))

# --- Database / files ---
# Shared with the dashboard server and exporter; see common/paths.py.
# STATE_JSON_PATH lands on tmpfs on the Pi, so the per-tick live snapshot
# stops writing ~8 GB/day to the SSD. FIELD_STATE_PATH stays durable.
from common.paths import DB_PATH, DB_DIR, STATE_JSON_PATH, FIELD_STATE_PATH


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_line(line):
    """
    Turn one serial line into a sample dict, or None to skip it.

    Skips: blank lines, non-JSON, "system" status/scan lines, and any line
    missing light_lux or sound_rms.
    """
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "system" in data:
        return None
    if "light_lux" not in data or "sound_rms" not in data:
        return None
    return data


def emitter_to_brightness(activation, led_max):
    """Map emitter activation (0-1) to an LED brightness, capped by led_max."""
    brightness = int(round(activation * 255))
    return clamp(brightness, 0, led_max)


def response_curve(value, floor=0.0, gain=1.0, exponent=1.0):
    """
    Map a normalized sensor value into behavioral intensity.

    The normalizer answers "where is this inside the recent sensor range?" This
    response curve answers "how strongly should the organism feel it?"
    """
    value = clamp(value, 0.0, 1.0)
    floor = clamp(floor, 0.0, 0.95)
    if value <= floor:
        return 0.0
    scaled = (value - floor) / (1.0 - floor)
    return clamp((scaled ** exponent) * gain, 0.0, 1.0)


def write_json_atomic(path, payload):
    """Best-effort atomic write. Never raises into the main loop."""
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as out:
            json.dump(payload, out)
        os.replace(tmp_path, path)
    except OSError:
        pass


def setup_database(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # One row per field tick: the inputs and the headline output.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS field_tick_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        tick INTEGER NOT NULL,
        sound_norm REAL,
        sound_linear REAL,
        light_norm REAL,
        emitter_activation REAL,
        sent_brightness INTEGER,
        energy_reserve REAL,
        energy_avg REAL,
        fatigue_avg REAL,
        memory_pressure REAL,
        sleep_mode TEXT,
        active_cells INTEGER,
        resting_cells INTEGER,
        dormant_cells INTEGER,
        deep_sleep_cells INTEGER,
        live_connections INTEGER,
        pruned_connections INTEGER,
        events_count INTEGER
    )
    """)

    # Sampled per-cell observations. The cell itself is no longer the memory
    # container; relevance is derived from structural links.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cell_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        tick INTEGER NOT NULL,
        cell INTEGER NOT NULL,
        cell_type TEXT,
        activation REAL,
        pressure REAL,
        energy REAL,
        fatigue REAL,
        relevance REAL,
        ripple REAL,
        state TEXT,
        sleep_state TEXT,
        tick_interval INTEGER,
        size REAL,
        homeo_gain REAL
    )
    """)

    # Periodic snapshot of connection weights for viewer playback.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS weight_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        tick INTEGER NOT NULL,
        cell_a INTEGER NOT NULL,
        cell_b INTEGER NOT NULL,
        weight REAL,
        age INTEGER,
        usage_count INTEGER,
        pressure_association REAL,
        last_active_tick INTEGER
    )
    """)

    # Medium-term event summaries: pressure spikes and sleep consolidation.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS field_event_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        tick INTEGER NOT NULL,
        event_type TEXT,
        significance REAL,
        sound_norm REAL,
        light_norm REAL,
        primary_cell INTEGER,
        details_json TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sleep_summary_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        start_tick INTEGER,
        end_tick INTEGER,
        duration_ticks INTEGER,
        reason TEXT,
        events_reviewed INTEGER,
        links_reinforced INTEGER,
        links_pruned INTEGER,
        energy_before REAL,
        energy_after REAL,
        memory_pressure_before REAL,
        memory_pressure_after REAL,
        details_json TEXT
    )
    """)

    ensure_columns(cur, "field_tick_log", [
        ("sound_linear", "REAL"),
        ("energy_reserve", "REAL"),
        ("energy_avg", "REAL"),
        ("fatigue_avg", "REAL"),
        ("memory_pressure", "REAL"),
        ("sleep_mode", "TEXT"),
        ("active_cells", "INTEGER"),
        ("resting_cells", "INTEGER"),
        ("dormant_cells", "INTEGER"),
        ("deep_sleep_cells", "INTEGER"),
        ("live_connections", "INTEGER"),
        ("pruned_connections", "INTEGER"),
        ("events_count", "INTEGER"),
    ])
    ensure_columns(cur, "cell_log", [
        ("ripple", "REAL"),
        ("energy", "REAL"),
        ("fatigue", "REAL"),
        ("relevance", "REAL"),
        ("state", "TEXT"),
        ("sleep_state", "TEXT"),
        ("tick_interval", "INTEGER"),
    ])
    ensure_columns(cur, "weight_log", [
        ("age", "INTEGER"),
        ("usage_count", "INTEGER"),
        ("pressure_association", "REAL"),
        ("last_active_tick", "INTEGER"),
    ])

    conn.commit()
    return conn, cur


def ensure_columns(cur, table, columns):
    existing = {
        row[1]
        for row in cur.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, definition in columns:
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def make_led_sender(transport):
    """Return a send(brightness) that only writes when the value changes."""
    last = {"command": None}

    def send(brightness):
        brightness = clamp(int(brightness), 0, 255)
        command = f"LED:{brightness}\n"
        if command == last["command"]:
            return brightness
        transport.write(command.encode("utf-8"))
        last["command"] = command
        return brightness

    return send


class TransportError(Exception):
    """Raised when the ESP transport cannot be opened or read."""


class SerialTransport:
    def __init__(self, port, baud):
        import serial
        from serial.serialutil import SerialException

        try:
            self.connection = serial.Serial(port, baud, timeout=0.2)
        except SerialException as error:
            raise TransportError(str(error)) from error
        self.description = f"serial:{port} @ {baud}"

    def readline(self):
        from serial.serialutil import SerialException

        try:
            return self.connection.readline().decode("utf-8", errors="ignore").strip()
        except SerialException as error:
            raise TransportError(str(error)) from error

    def write(self, payload):
        from serial.serialutil import SerialException

        try:
            self.connection.write(payload)
        except SerialException as error:
            raise TransportError(str(error)) from error

    def close(self):
        self.connection.close()


class TcpTransport:
    def __init__(self, host, port):
        self.host = host
        self.port = int(port)
        self.buffer = bytearray()
        try:
            self.connection = socket.create_connection(
                (self.host, self.port),
                timeout=TCP_CONNECT_TIMEOUT_SECONDS,
            )
            self.connection.settimeout(TCP_READ_TIMEOUT_SECONDS)
        except OSError as error:
            raise TransportError(str(error)) from error
        self.description = f"tcp://{self.host}:{self.port}"

    def readline(self):
        while True:
            newline_index = self.buffer.find(b"\n")
            if newline_index >= 0:
                line = self.buffer[:newline_index]
                del self.buffer[:newline_index + 1]
                return line.decode("utf-8", errors="ignore").strip()

            try:
                chunk = self.connection.recv(512)
            except socket.timeout:
                return ""
            except OSError as error:
                raise TransportError(str(error)) from error

            if chunk == b"":
                raise TransportError("TCP connection closed by ESP")
            self.buffer.extend(chunk)

    def write(self, payload):
        try:
            self.connection.sendall(payload)
        except OSError as error:
            raise TransportError(str(error)) from error

    def close(self):
        self.connection.close()


def parse_tcp_target(target):
    """Return (host, port) from tcp://host:port or host:port."""
    if target.startswith("tcp://"):
        target = target[len("tcp://"):]
    if "/" in target:
        target = target.split("/", 1)[0]
    if ":" in target:
        host, port_text = target.rsplit(":", 1)
        return host, int(port_text)
    return target, DEFAULT_TCP_PORT


def is_tcp_target(target):
    return target.startswith("tcp://") or (
        ":" in target and not target.startswith("/dev/") and not target.startswith("/dev/cu.")
    )


def open_esp_transport(target):
    if is_tcp_target(target):
        host, port = parse_tcp_target(target)
        return TcpTransport(host, port)
    return SerialTransport(target, BAUD)


def reconnect_esp_transport(target, stop):
    while not stop["requested"]:
        try:
            esp = open_esp_transport(target)
            print(f"ESP reconnected: {esp.description}")
            return esp
        except TransportError as error:
            print(f"Reconnect failed: {error}. Retrying in {TCP_RECONNECT_SECONDS:.0f}s.")
            sleep(TCP_RECONNECT_SECONDS)
    return None


def should_log_cells(state, tick):
    # Events no longer trigger a full 111-row cell dump: field_event_log
    # already records each event's top cells, and in a noisy room the old
    # behavior logged every cell every tick.
    if tick % max(1, CELL_LOG_EVERY_TICKS) == 0:
        return True
    if state.get("sleep_summary"):
        return True
    metabolism = state.get("metabolism", {})
    return metabolism.get("mode") == "sleep" and tick % 5 == 0


def log_tick(cur, logged_at, state, sound_norm, sound_linear, light_norm, sent_brightness, tick):
    metabolism = state.get("metabolism", {})
    counts = state.get("state_counts") or state.get("sleep_counts") or {}

    cur.execute("""
    INSERT INTO field_tick_log (
        logged_at, tick, sound_norm, sound_linear, light_norm, emitter_activation, sent_brightness,
        energy_reserve, energy_avg, fatigue_avg, memory_pressure, sleep_mode,
        active_cells, resting_cells, dormant_cells, deep_sleep_cells,
        live_connections, pruned_connections, events_count
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        logged_at, tick,
        round(sound_norm, 4), round(sound_linear, 4), round(light_norm, 4),
        state["emitter_activation"], sent_brightness,
        metabolism.get("energy_reserve"),
        metabolism.get("energy_avg"),
        metabolism.get("fatigue_avg"),
        metabolism.get("memory_pressure"),
        metabolism.get("mode"),
        counts.get("active"),
        counts.get("resting"),
        counts.get("dormant"),
        counts.get("deep_sleep"),
        metabolism.get("live_connections"),
        metabolism.get("pruned_connections"),
        len(state.get("events") or []),
    ))

    if not should_log_cells(state, tick):
        return

    cur.executemany("""
    INSERT INTO cell_log (
        logged_at, tick, cell, cell_type, activation, pressure, energy,
        fatigue, relevance, ripple, state, sleep_state, tick_interval,
        size, homeo_gain
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            logged_at, tick, c["n"], c["type"],
            c["activation"], c["pressure"], c.get("energy"), c.get("fatigue"),
            c.get("relevance"), c.get("ripple"), c.get("state"),
            c.get("sleep_state"), c.get("tick_interval"),
            c["size"], c["homeo_gain"],
        )
        for c in state["cells"]
    ])


def log_weights(cur, logged_at, state, tick):
    cur.executemany("""
    INSERT INTO weight_log (
        logged_at, tick, cell_a, cell_b, weight, age, usage_count,
        pressure_association, last_active_tick
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            logged_at, tick, w["a"], w["b"], w["weight"],
            w.get("age"), w.get("usage_count"), w.get("pressure_association"),
            w.get("last_active_tick"),
        )
        for w in state["connections"]
    ])


def log_events(cur, logged_at, state):
    rows = []
    for event in state.get("events") or []:
        cells = event.get("cells") or []
        rows.append((
            logged_at,
            event.get("tick"),
            event.get("type"),
            event.get("significance"),
            event.get("sound_norm"),
            event.get("light_norm"),
            cells[0].get("n") if cells else None,
            json.dumps(event, separators=(",", ":")),
        ))
    if not rows:
        return
    cur.executemany("""
    INSERT INTO field_event_log (
        logged_at, tick, event_type, significance, sound_norm, light_norm,
        primary_cell, details_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)


def log_sleep_summary(cur, logged_at, summary):
    if not summary:
        return
    cur.execute("""
    INSERT INTO sleep_summary_log (
        logged_at, start_tick, end_tick, duration_ticks, reason,
        events_reviewed, links_reinforced, links_pruned,
        energy_before, energy_after, memory_pressure_before,
        memory_pressure_after, details_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        logged_at,
        summary.get("start_tick"),
        summary.get("end_tick"),
        summary.get("duration_ticks"),
        summary.get("reason"),
        summary.get("events_reviewed"),
        summary.get("links_reinforced"),
        summary.get("links_pruned"),
        summary.get("energy_before"),
        summary.get("energy_after"),
        summary.get("memory_pressure_before"),
        summary.get("memory_pressure_after"),
        json.dumps(summary, separators=(",", ":")),
    ))


def apply_retention_policy(cur):
    """Optional conservative retention for temporary observation tables."""
    if not RETENTION_ENABLED:
        return

    temporary_cutoff = (datetime.now() - timedelta(hours=TEMPORARY_RETENTION_HOURS)).isoformat()
    event_cutoff = (datetime.now() - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
    for table in ("cell_log", "field_tick_log", "weight_log"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE logged_at < ?", (temporary_cutoff,))
        except sqlite3.Error:
            pass
    for table in ("field_event_log", "sleep_summary_log"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE logged_at < ?", (event_cutoff,))
        except sqlite3.Error:
            pass


def main():
    if len(sys.argv) > 1:
        transport_target = sys.argv[1]
    elif os.environ.get("CREATURE_ESP_HOST"):
        host = os.environ["CREATURE_ESP_HOST"]
        port = int(os.environ.get("CREATURE_ESP_PORT", DEFAULT_TCP_PORT))
        transport_target = f"tcp://{host}:{port}"
    else:
        transport_target = os.environ.get("CREATURE_SERIAL_PORT", DEFAULT_PORT)

    if DB_DIR:
        os.makedirs(DB_DIR, exist_ok=True)
    state_dir = os.path.dirname(STATE_JSON_PATH)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)

    conn, cur = setup_database(DB_PATH)

    # Build the field and reload its slow state if it has lived before.
    field = build_field()
    loaded = load_field(field, FIELD_STATE_PATH)
    if loaded is not None:
        print(f"Loaded field state from {loaded.get('saved_at', 'unknown time')} "
              f"(tick {loaded.get('tick', 0)}).")
    else:
        print("No saved field state. Starting fresh.")

    light_norm = RollingNormalizer(LIGHT_WINDOW_SECONDS, LIGHT_EMA_ALPHA, LIGHT_MIN_RANGE)
    sound_norm = RollingNormalizer(SOUND_WINDOW_SECONDS, SOUND_EMA_ALPHA, SOUND_MIN_RANGE)

    # Stop cleanly on Ctrl-C (SIGINT) and `systemctl stop` (SIGTERM).
    stop = {"requested": False}

    def request_stop(signum, frame):
        stop["requested"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        # A short read timeout lets the loop wake often enough to tick at 1 Hz.
        esp = open_esp_transport(transport_target)
    except TransportError as error:
        print(f"Could not open ESP transport {transport_target}.")
        print("USB: close any serial monitor, unplug/replug the ESP, then retry.")
        print("WiFi: confirm the ESP is on the same network and the IP/hostname is reachable.")
        print("Usage: python collector/collector.py /dev/ttyUSB0")
        print("   or: python collector/collector.py tcp://creature-esp.local:7777")
        raise error

    led_send = make_led_sender(esp)

    # Always save the slow field state on exit, however we leave.
    atexit.register(lambda: save_field(field, FIELD_STATE_PATH))

    print(f"Collector running ({FIELD_VERSION} metabolism + structural memory).")
    print(f"ESP transport: {esp.description}")
    print(f"Database: {DB_PATH}")
    print(f"Live snapshot: {STATE_JSON_PATH}")
    print(f"Field tick: {TICK_SECONDS:.0f} Hz inverse, LED max: {LED_MAX_BRIGHTNESS}")
    print(f"Cell log cadence: every {CELL_LOG_EVERY_TICKS} ticks; "
          f"weight log cadence: every {WEIGHT_LOG_EVERY_TICKS} ticks; "
          f"commit cadence: every {COMMIT_EVERY_TICKS} ticks.")

    next_tick = monotonic()

    while not stop["requested"]:
        try:
            line = esp.readline()
        except TransportError as error:
            if not is_tcp_target(transport_target):
                print("ESP serial connection lost. Check USB, then restart the collector.")
                raise error
            print(f"ESP WiFi connection lost: {error}. Reconnecting...")
            try:
                esp.close()
            except OSError:
                pass
            esp = reconnect_esp_transport(transport_target, stop)
            if esp is None:
                break
            led_send = make_led_sender(esp)
            continue

        now = monotonic()

        sample = parse_line(line)
        if sample is not None:
            light_norm.add(float(sample["light_lux"]), now)
            sound_norm.add(float(sample["sound_rms"]), now)

        if now < next_tick:
            continue

        # --- one field tick ---
        sound_linear = sound_norm.normalized()
        sound_value = response_curve(
            sound_linear,
            SOUND_RESPONSE_FLOOR,
            SOUND_RESPONSE_GAIN,
            SOUND_RESPONSE_EXPONENT,
        )
        light_value = light_norm.normalized()

        state = field.step(sound_value, light_value)

        brightness = emitter_to_brightness(field.emitter_activation, LED_MAX_BRIGHTNESS)
        try:
            sent_brightness = led_send(brightness)
        except TransportError as error:
            if not is_tcp_target(transport_target):
                print("Could not send LED command over serial. Check USB, then restart the collector.")
                raise error
            print(f"Could not send LED command over WiFi: {error}. Reconnecting...")
            sent_brightness = None
            try:
                esp.close()
            except OSError:
                pass
            esp = reconnect_esp_transport(transport_target, stop)
            if esp is None:
                break
            led_send = make_led_sender(esp)

        logged_at = datetime.now().isoformat()
        tick = field.tick_count

        if tick % STATUS_PRINT_EVERY_TICKS == 0:
            metabolism = state.get("metabolism", {})
            counts = state.get("state_counts", {})
            count_text = (
                f"{counts.get('active', 0)}/"
                f"{counts.get('resting', 0)}/"
                f"{counts.get('dormant', 0)}/"
                f"{counts.get('deep_sleep', 0)}"
            )
            print(f"t{tick}  sound={sound_value:.2f}({sound_linear:.2f})  light={light_value:.2f}  "
                  f"C{EMITTER_ANCHOR}={state['emitter_activation']:.2f}  "
                  f"led={sent_brightness}  E={metabolism.get('energy_reserve', 0):.1f}  "
                  f"M={metabolism.get('memory_pressure', 0):.2f}  states={count_text}")

        # Live snapshot for the dashboard.
        snapshot = {
            "field_version": state.get("field_version", FIELD_VERSION),
            "updated_at": logged_at,
            "tick": tick,
            "sound_norm": round(sound_value, 4),
            "sound_linear": round(sound_linear, 4),
            "light_norm": round(light_value, 4),
            "sound_debug": {
                **sound_norm.debug(),
                "linear": round(sound_linear, 4),
                "response_floor": SOUND_RESPONSE_FLOOR,
                "response_gain": SOUND_RESPONSE_GAIN,
                "response_exponent": SOUND_RESPONSE_EXPONENT,
            },
            "light_debug": light_norm.debug(),
            "emitter_activation": state["emitter_activation"],
            "sent_brightness": sent_brightness,
            "cell_count": state.get("cell_count", CELL_COUNT),
            "anchors": state.get("anchors"),
            "layout": state.get("layout"),
            "state_counts": state.get("state_counts"),
            "sleep_counts": state.get("sleep_counts"),
            "metabolism": state.get("metabolism"),
            "recent_events": state.get("recent_events"),
            "events": state.get("events"),
            "sleep_summary": state.get("sleep_summary"),
            "cells": state["cells"],
            "connections": state["connections"],
        }
        write_json_atomic(STATE_JSON_PATH, snapshot)

        # History log. Commits are batched: WAL keeps readers happy, and a
        # power loss costs at most COMMIT_EVERY_TICKS seconds of observation
        # rows (the field state itself is saved separately).
        log_tick(cur, logged_at, state, sound_value, sound_linear, light_value, sent_brightness, tick)
        log_events(cur, logged_at, state)
        log_sleep_summary(cur, logged_at, state.get("sleep_summary"))
        if tick % WEIGHT_LOG_EVERY_TICKS == 0:
            log_weights(cur, logged_at, state, tick)
        if tick % RETENTION_EVERY_TICKS == 0:
            apply_retention_policy(cur)
        if tick % COMMIT_EVERY_TICKS == 0 or state.get("sleep_summary"):
            conn.commit()

        # Persist slow state periodically so a power loss costs little.
        if tick % SAVE_EVERY_TICKS == 0:
            try:
                save_field(field, FIELD_STATE_PATH)
            except OSError as error:
                print("Could not save field state:", error)

        # Schedule the next tick. If we fell behind, resync instead of bursting.
        next_tick += TICK_SECONDS
        if now > next_tick:
            next_tick = now + TICK_SECONDS

    print("Stop requested. Saving field state and shutting down cleanly.")
    save_field(field, FIELD_STATE_PATH)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
