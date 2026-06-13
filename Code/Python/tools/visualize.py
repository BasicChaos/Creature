"""
Creature v05.4 — Data Visualizer
Reads creature_raw_light_v054.db and generates a self-contained HTML file
with interactive Plotly charts.

Usage (from Code/Python):
    python tools/visualize.py
    python tools/visualize.py --db data/creature_raw_light_v054.db --out creature_viz.html
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Cell grid layout ────────────────────────────────────────────────────────
try:
    from mind import cell_field as cf
    COORDS = cf.COORDS
    SOUND_ANCHOR  = cf.SOUND_ANCHOR
    LIGHT_ANCHOR  = cf.LIGHT_ANCHOR
    EMITTER_ANCHOR = cf.EMITTER_ANCHOR
except Exception:
    COORDS = {}
    SOUND_ANCHOR = LIGHT_ANCHOR = EMITTER_ANCHOR = None

ANCHOR_IDS = {SOUND_ANCHOR, LIGHT_ANCHOR, EMITTER_ANCHOR} - {None}

# ── Colors ───────────────────────────────────────────────────────────────────
C_LIGHT    = "#f5c842"
C_SOUND    = "#42a5f5"
C_ENERGY   = "#66bb6a"
C_PRESSURE = "#ef5350"
C_ACTIVE   = "#66bb6a"
C_RESTING  = "#42a5f5"
C_DORMANT  = "#9e9e9e"
C_DEEP     = "#37474f"
C_SLEEP    = "rgba(100, 100, 200, 0.12)"


# ── Data loading ─────────────────────────────────────────────────────────────

def load_timeline(conn, max_points=4000):
    """Sample field_tick_log down to max_points rows."""
    total = conn.execute("SELECT COUNT(*) FROM field_tick_log").fetchone()[0]
    step  = max(1, total // max_points)
    rows  = conn.execute(f"""
        SELECT logged_at, tick, sound_norm, light_norm,
               energy_reserve, memory_pressure, sleep_mode,
               active_cells, resting_cells, dormant_cells, deep_sleep_cells,
               live_connections, pruned_connections
        FROM field_tick_log
        WHERE id % {step} = 0
        ORDER BY tick
    """).fetchall()
    cols = ["logged_at","tick","sound","light","energy","pressure","sleep_mode",
            "active","resting","dormant","deep_sleep","live_conn","pruned_conn"]
    return [dict(zip(cols, r)) for r in rows]


def load_sleep_cycles(conn):
    rows = conn.execute("""
        SELECT logged_at, start_tick, end_tick, duration_ticks,
               reason, links_reinforced, links_pruned,
               energy_before, energy_after,
               memory_pressure_before, memory_pressure_after
        FROM sleep_summary_log ORDER BY start_tick
    """).fetchall()
    cols = ["logged_at","start_tick","end_tick","duration",
            "reason","links_reinforced","links_pruned",
            "energy_before","energy_after","pressure_before","pressure_after"]
    return [dict(zip(cols, r)) for r in rows]


def load_final_weights(conn):
    max_tick = conn.execute("SELECT MAX(tick) FROM weight_log").fetchone()[0]
    rows = conn.execute("""
        SELECT cell_a, cell_b, weight, usage_count, age
        FROM weight_log WHERE tick = ?
    """, (max_tick,)).fetchall()
    return rows, max_tick


def load_cell_heatmap(conn, n_ticks=800):
    """Last n_ticks worth of cell activation data (sampled)."""
    max_tick = conn.execute("SELECT MAX(tick) FROM cell_log").fetchone()[0]
    min_tick = max(1, max_tick - n_ticks)
    # Sample every ~4th tick to keep it manageable
    rows = conn.execute("""
        SELECT tick, cell, activation
        FROM cell_log
        WHERE tick >= ? AND tick % 4 = 0
        ORDER BY tick, cell
    """, (min_tick,)).fetchall()
    return rows, min_tick, max_tick


def load_event_types(conn):
    rows = conn.execute("""
        SELECT event_type, COUNT(*) as cnt
        FROM field_event_log GROUP BY event_type ORDER BY cnt DESC
    """).fetchall()
    return rows


# ── Chart builders ────────────────────────────────────────────────────────────

def chart_timeline(timeline, sleep_cycles):
    """4-panel overview: light/sound, energy/pressure, cell states, connections."""
    times   = [r["logged_at"] for r in timeline]
    ticks   = [r["tick"]      for r in timeline]

    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        subplot_titles=[
            "Light & Sound",
            "Energy & Memory Pressure",
            "Cell States",
            "Live Connections",
        ],
        vertical_spacing=0.06,
        row_heights=[0.22, 0.22, 0.30, 0.20],
    )

    # Sleep shading on all panels
    for s in sleep_cycles:
        t0 = s["logged_at"]
        for row in range(1, 5):
            fig.add_vrect(x0=t0, x1=t0, fillcolor=C_SLEEP,
                          layer="below", line_width=0, row=row, col=1)

    # Panel 1: light + sound
    fig.add_trace(go.Scatter(x=times, y=[r["light"] for r in timeline],
        name="Light", line=dict(color=C_LIGHT, width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=times, y=[r["sound"] for r in timeline],
        name="Sound", line=dict(color=C_SOUND, width=1)), row=1, col=1)

    # Panel 2: energy + pressure
    fig.add_trace(go.Scatter(x=times, y=[r["energy"] for r in timeline],
        name="Energy", line=dict(color=C_ENERGY, width=1)), row=2, col=1)
    fig.add_trace(go.Scatter(x=times, y=[r["pressure"] for r in timeline],
        name="Mem Pressure", line=dict(color=C_PRESSURE, width=1),
        yaxis="y2"), row=2, col=1)

    # Panel 3: stacked cell states
    fig.add_trace(go.Scatter(x=times, y=[r["active"] for r in timeline],
        name="Active", stackgroup="states",
        line=dict(width=0), fillcolor="rgba(102,187,106,0.73)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=[r["resting"] for r in timeline],
        name="Resting", stackgroup="states",
        line=dict(width=0), fillcolor="rgba(66,165,245,0.73)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=[r["dormant"] for r in timeline],
        name="Dormant", stackgroup="states",
        line=dict(width=0), fillcolor="rgba(158,158,158,0.73)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=[r["deep_sleep"] for r in timeline],
        name="Deep Sleep", stackgroup="states",
        line=dict(width=0), fillcolor="rgba(55,71,79,0.73)"), row=3, col=1)

    # Panel 4: live connections
    fig.add_trace(go.Scatter(x=times, y=[r["live_conn"] for r in timeline],
        name="Live Connections", line=dict(color="#ab47bc", width=1)), row=4, col=1)

    fig.update_layout(
        height=800,
        title="Creature Overview — 7-day run",
        template="plotly_dark",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.05),
    )
    return fig


def chart_sleep(sleep_cycles):
    """Sleep cycle summary: duration, reason, links reinforced/pruned."""
    if not sleep_cycles:
        return None

    reasons      = [s["reason"] for s in sleep_cycles]
    durations    = [s["duration"] for s in sleep_cycles]
    reinforced   = [s["links_reinforced"] for s in sleep_cycles]
    pruned       = [s["links_pruned"] for s in sleep_cycles]
    pressure_delta = [round(s["pressure_after"] - s["pressure_before"], 4)
                      for s in sleep_cycles]
    times        = [s["logged_at"] for s in sleep_cycles]

    reason_color = {
        "low_energy": "#66bb6a",
        "memory_pressure": "#ef5350",
        "memory_pressure+low_energy": "#ff7043",
    }
    colors = [reason_color.get(r, "#9e9e9e") for r in reasons]

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        subplot_titles=["Sleep Duration (ticks)", "Links Reinforced / Pruned", "Memory Pressure Δ"],
        vertical_spacing=0.1,
    )

    fig.add_trace(go.Bar(x=times, y=durations, name="Duration",
        marker_color=colors, showlegend=False), row=1, col=1)

    fig.add_trace(go.Bar(x=times, y=reinforced, name="Reinforced",
        marker_color="#66bb6a"), row=2, col=1)
    fig.add_trace(go.Bar(x=times, y=pruned, name="Pruned",
        marker_color="#ef5350"), row=2, col=1)

    fig.add_trace(go.Bar(x=times, y=pressure_delta, name="Pressure Δ",
        marker_color=["#ef5350" if d > 0 else "#66bb6a" for d in pressure_delta],
        showlegend=False), row=3, col=1)

    fig.update_layout(
        height=600,
        title=f"Sleep Cycles ({len(sleep_cycles)} total)",
        template="plotly_dark",
        barmode="overlay",
    )
    return fig


def chart_network(weights, max_tick):
    """Connection network at final tick. Nodes = cells, edges = weight strength."""
    if not COORDS:
        return None

    threshold = 0.05
    live = [(a, b, w, u) for a, b, w, u, *_ in weights if w > threshold]

    # Edge traces (one per edge, colored by weight)
    max_w = max((w for _, _, w, _ in live), default=1)
    edge_traces = []
    for a, b, w, usage in live:
        if a not in COORDS or b not in COORDS:
            continue
        r0, c0 = COORDS[a]
        r1, c1 = COORDS[b]
        alpha = 0.2 + 0.7 * (w / max_w)
        width = 0.5 + 4.0 * (w / max_w)
        norm_w = w / max_w
        red   = int(255 * norm_w)
        blue  = int(255 * (1 - norm_w))
        color = f"rgba({red},100,{blue},{alpha:.2f})"
        edge_traces.append(go.Scatter(
            x=[c0, c1, None], y=[-r0, -r1, None],
            mode="lines",
            line=dict(width=width, color=color),
            hoverinfo="skip",
            showlegend=False,
        ))

    # Node trace
    node_ids   = sorted(COORDS.keys())
    node_x     = [COORDS[i][1] for i in node_ids]
    node_y     = [-COORDS[i][0] for i in node_ids]
    node_color = []
    node_label = []
    node_size  = []
    for i in node_ids:
        if i == SOUND_ANCHOR:
            node_color.append("#42a5f5")
            node_label.append(f"Cell {i}<br>SOUND anchor")
            node_size.append(18)
        elif i == LIGHT_ANCHOR:
            node_color.append("#f5c842")
            node_label.append(f"Cell {i}<br>LIGHT anchor")
            node_size.append(18)
        elif i == EMITTER_ANCHOR:
            node_color.append("#ef5350")
            node_label.append(f"Cell {i}<br>EMITTER anchor")
            node_size.append(18)
        else:
            node_color.append("#78909c")
            node_label.append(f"Cell {i}")
            node_size.append(10)

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        marker=dict(color=node_color, size=node_size,
                    line=dict(width=1, color="rgba(255,255,255,0.2)")),
        text=[str(i) for i in node_ids],
        textposition="top center",
        textfont=dict(size=7, color="#aaaaaa"),
        hovertext=node_label,
        hoverinfo="text",
        name="Cells",
    )

    fig = go.Figure(data=edge_traces + [node_trace])
    fig.update_layout(
        height=700,
        title=f"Connection Network — tick {max_tick} (live connections > {threshold})<br>"
              f"<span style='font-size:12px'>Blue=sound anchor · Yellow=light anchor · Red=emitter anchor · "
              f"Edge color: red=strong, blue=weak</span>",
        template="plotly_dark",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, scaleanchor="x"),
        showlegend=False,
    )
    return fig


def chart_cell_heatmap(rows, min_tick, max_tick):
    """Activation heatmap: cells × ticks (recent window)."""
    if not rows:
        return None

    ticks = sorted(set(r[0] for r in rows))
    cells = sorted(set(r[1] for r in rows))

    # Build matrix
    cell_idx = {c: i for i, c in enumerate(cells)}
    tick_idx = {t: i for i, t in enumerate(ticks)}
    import numpy as np
    Z = np.zeros((len(cells), len(ticks)))
    for tick, cell, activation in rows:
        if cell in cell_idx and tick in tick_idx:
            Z[cell_idx[cell], tick_idx[tick]] = activation

    fig = go.Figure(go.Heatmap(
        z=Z,
        x=ticks,
        y=cells,
        colorscale="Viridis",
        colorbar=dict(title="Activation"),
    ))
    fig.update_layout(
        height=600,
        title=f"Cell Activation Heatmap — last {max_tick - min_tick} ticks",
        template="plotly_dark",
        xaxis_title="Tick",
        yaxis_title="Cell ID",
        yaxis=dict(autorange="reversed"),
    )
    return fig


# ── HTML assembly ─────────────────────────────────────────────────────────────

def build_html(figs, db_path, total_ticks, n_sleep):
    parts = []
    for fig in figs:
        if fig is not None:
            parts.append(fig.to_html(full_html=False, include_plotlyjs=False))

    chart_divs = "\n<hr style='border-color:#333;margin:40px 0'>\n".join(parts)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Creature Visualization</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  body {{ background: #1a1a2e; color: #e0e0e0; font-family: monospace;
         margin: 0; padding: 20px; }}
  h1 {{ color: #f5c842; font-size: 1.4em; margin-bottom: 4px; }}
  .meta {{ color: #888; font-size: 0.85em; margin-bottom: 30px; }}
  hr {{ border: none; border-top: 1px solid #333; margin: 40px 0; }}
</style>
</head>
<body>
<h1>Creature v05.4 — Data Visualization</h1>
<div class="meta">
  DB: {db_path} &nbsp;|&nbsp;
  Ticks: {total_ticks:,} &nbsp;|&nbsp;
  Sleep cycles: {n_sleep}
</div>
{chart_divs}
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db",  default="data/creature_raw_light_v054.db")
    p.add_argument("--out", default="data/creature_viz.html")
    p.add_argument("--heatmap-ticks", type=int, default=800,
                   help="How many recent ticks to show in the cell heatmap")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / args.out

    print(f"Loading {db_path} ...")
    conn = sqlite3.connect(db_path)

    print("  timeline ...")
    timeline     = load_timeline(conn)
    print("  sleep cycles ...")
    sleep_cycles = load_sleep_cycles(conn)
    print("  weights ...")
    weights, max_weight_tick = load_final_weights(conn)
    print("  cell heatmap ...")
    heatmap_rows, min_t, max_t = load_cell_heatmap(conn, args.heatmap_ticks)

    total_ticks = conn.execute("SELECT MAX(tick) FROM field_tick_log").fetchone()[0]
    conn.close()

    print("Building charts ...")
    figs = [
        chart_timeline(timeline, sleep_cycles),
        chart_sleep(sleep_cycles),
        chart_network(weights, max_weight_tick),
        chart_cell_heatmap(heatmap_rows, min_t, max_t),
    ]

    print(f"Writing {out_path} ...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(figs, db_path.name, total_ticks, len(sleep_cycles)))
    print(f"Done — open {out_path} in a browser.")


if __name__ == "__main__":
    main()
