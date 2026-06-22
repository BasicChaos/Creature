"""
Expression preview: see the strip and hear the voice before any hardware.

Runs the cell field offline (synthetic scenario or a recorded DB), applies the
v06 decoder (Creature v06 - expression layer.md), and renders:

  - preview.png : the LED strip over the whole run (filmstrip) + the A/B/T signals
  - voice.wav   : the synthesized voice for the run
  - player.html : an animated strip that plays in sync with the voice

The decoder is read-only on the field. This tool changes nothing about how the
field behaves; it only maps field state to outputs, the same map the collector
will use to drive the real strip and speaker.

Run from Code/Python:

    python tools/expression_preview.py --scenario day --ticks 360 --out tools/preview
    python tools/expression_preview.py --scenario bursts --ticks 300
    python tools/expression_preview.py --scenario quiet  --ticks 300

Nothing here touches the Pi, the ESP, or the live database.
"""

import argparse
import base64
import math
import os
import sys
import wave
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

# Make `mind` importable when run from Code/Python.
PROJECT_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PYTHON_ROOT))

from mind.cell_field_v06 import build_field  # noqa: E402
from mind.expression_v06 import ExpressionDecoderV06  # noqa: E402


# --- Decoder knobs (the same numbers the collector decoder will expose) -------
KNOBS = dict(
    PIXELS=16,
    # arousal energy gate, as a fraction of the field's max reserve
    GATE_LOW=0.05,
    GATE_HIGH=0.40,
    # how much to amplify (small) cell activations into visible brightness
    ACT_GAIN=4.0,
    FLOOR=0.05,          # faint base so tissue is never pure black
    WHITE_GLOW=70.0,     # arousal -> soft white added to every pixel (0-255)
    EVENT_SIG_MIN=0.8,   # only flash/chirp on events at least this significant
    # tempo
    RIPPLE_REF=0.18,     # mean abs ripple that counts as "fully agitated"
    PULSE_BASE=0.015,    # travelling pulse speed floor (pixel-fraction / tick)
    PULSE_SPEED=0.22,    # extra pulse speed from tempo
    PULSE_WIDTH=0.10,
    SHIMMER=22.0,        # tempo-scaled per-pixel jitter (0-255)
    EVENT_WIDTH=1.6,     # event flash width, in field columns
    # voice
    F_LOW=220.0,
    F_HIGH=440.0,
    # color poles (RGB)
    COOL=(40, 170, 230),
    WARM=(255, 150, 40),
    PULSE_RGB=(255, 245, 220),
    EVENT_RGB=(255, 255, 255),
    LED_CAP=200,         # brightness cap for the preview (real strip uses ~80)
)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def smoothstep(x, lo, hi):
    if hi <= lo:
        return 0.0
    t = clamp((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def scenario_inputs(name, ticks, rng):
    """Yield v06 sense dicts in 0-1 for a named synthetic scenario."""
    for t in range(ticks):
        weather = 0.42 + 0.08 * math.sin(2 * math.pi * t / max(1, ticks) + 0.4)
        motion = 0.0
        if name == "day":
            # slow day/night light, sparse sound bursts (someone in the room)
            light = 0.5 + 0.45 * math.sin(2 * math.pi * t / ticks - math.pi / 2)
            sound = 0.0
            if rng.random() < 0.06:
                sound = rng.uniform(0.4, 1.0)
                motion = rng.uniform(0.15, 0.55)
            elif rng.random() < 0.15:
                sound = rng.uniform(0.05, 0.2)
        elif name == "bursts":
            light = 0.4 + 0.05 * math.sin(2 * math.pi * t / 120)
            sound = rng.uniform(0.5, 1.0) if rng.random() < 0.25 else rng.uniform(0.0, 0.1)
            motion = rng.uniform(0.35, 1.0) if rng.random() < 0.18 else rng.uniform(0.0, 0.08)
        elif name == "quiet":
            light = 0.2 + 0.02 * math.sin(2 * math.pi * t / 300)
            sound = rng.uniform(0.0, 0.05)
            motion = rng.uniform(0.0, 0.03)
        else:
            raise ValueError(f"unknown scenario: {name}")
        yield {
            "sound": clamp(sound, 0, 1),
            "light": clamp(light, 0, 1),
            "motion": clamp(motion, 0, 1),
            "weather": clamp(weather, 0, 1),
        }


def synth_voice(A, B, T, events, secs_per_tick, sr, knobs):
    """Build a mono waveform from the per-tick voice parameters."""
    k = knobs
    spt = int(sr * secs_per_tick)
    n = len(A)
    out = np.zeros(n * spt, dtype=np.float64)
    phase = 0.0
    prev_f = k["F_LOW"]
    prev_amp = 0.0
    for i in range(n):
        b = (B[i] + 1) / 2
        f_target = k["F_LOW"] * (k["F_HIGH"] / k["F_LOW"]) ** b
        amp_target = A[i]
        fs = np.linspace(prev_f, f_target, spt)
        amps = np.linspace(prev_amp, amp_target, spt)
        inc = 2 * np.pi * fs / sr
        ph = phase + np.cumsum(inc)
        phase = ph[-1] % (2 * np.pi)
        tone = np.sin(ph) + 0.25 * np.sin(2 * ph)
        noise = np.random.uniform(-1, 1, spt)
        rough = T[i]
        sig = amps * ((1 - 0.7 * rough) * tone + 0.7 * rough * noise)
        out[i * spt:(i + 1) * spt] = sig
        prev_f, prev_amp = f_target, amp_target

    peak = np.max(np.abs(out)) or 1.0
    out = (out / peak) * 0.9
    return (out * 32767).astype(np.int16)


def write_wav(path, samples, sr):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())


def rgbw_to_rgb(px):
    """Flatten an (r,g,b,w) pixel to RGB for display: white lifts all channels."""
    r, g, b, wch = px
    return (
        min(255, r + int(wch * 0.5)),
        min(255, g + int(wch * 0.5)),
        min(255, b + int(wch * 0.45)),
    )


def render_png(path, rows, A, B, T, scenario):
    ticks = len(rows)
    n = len(rows[0])
    img = np.zeros((ticks, n, 3), dtype=np.uint8)
    for i, row in enumerate(rows):
        for j, px in enumerate(row):
            img[i, j] = rgbw_to_rgb(px)

    fig, (ax_strip, ax_sig) = plt.subplots(
        2, 1, figsize=(9, 7), gridspec_kw={"height_ratios": [3, 1.4]}
    )
    ax_strip.imshow(img, aspect="auto", interpolation="nearest", origin="upper")
    ax_strip.set_title(f"LED strip over time  ({scenario}, {ticks} ticks, {n} pixels)")
    ax_strip.set_xlabel("pixel  (sound end -> light end)")
    ax_strip.set_ylabel("time (ticks)  ->")

    xs = range(ticks)
    ax_sig.plot(xs, A, label="arousal", color="#e0683c")
    ax_sig.plot(xs, [(b + 1) / 2 for b in B], label="balance (warm=1)", color="#c026d3")
    ax_sig.plot(xs, T, label="tempo", color="#2563eb")
    ax_sig.set_ylim(-0.02, 1.02)
    ax_sig.set_xlim(0, ticks)
    ax_sig.set_xlabel("time (ticks)")
    ax_sig.legend(loc="upper right", fontsize=8, ncol=3)
    ax_sig.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def write_html(path, rows, wav_path, secs_per_tick):
    n = len(rows[0])
    data = [[list(px) for px in row] for row in rows]
    wav_b64 = base64.b64encode(Path(wav_path).read_bytes()).decode("ascii")
    ms = int(secs_per_tick * 1000)
    html = """<!doctype html><meta charset="utf-8">
<title>Creature expression preview</title>
<style>
 body{font-family:ui-sans-serif,system-ui,sans-serif;background:#111;color:#ddd;padding:24px}
 #strip{display:flex;gap:3px;margin:18px 0;height:90px}
 .px{flex:1;border-radius:4px}
 button{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid #555;background:#222;color:#eee;cursor:pointer}
 .meta{font-size:13px;color:#999}
</style>
<h2>Creature expression preview</h2>
<p class="meta">The strip is the field's skin. Press play to run it in sync with the voice.</p>
<button id="play">play</button>
<div id="strip"></div>
<audio id="aud" src="data:audio/wav;base64,__WAV__"></audio>
<script>
const ROWS=__ROWS__, MS=__MS__, N=__N__;
const strip=document.getElementById('strip');
const px=[];
for(let i=0;i<N;i++){const d=document.createElement('div');d.className='px';strip.appendChild(d);px.push(d);}
function draw(row){for(let i=0;i<N;i++){const p=row[i];const w=Math.round(p[3]*0.5);
 px[i].style.background='rgb('+Math.min(255,p[0]+w)+','+Math.min(255,p[1]+w)+','+Math.min(255,p[2]+w)+')';}}
draw(ROWS[0]);
let t=0,timer=null;const aud=document.getElementById('aud');
document.getElementById('play').onclick=()=>{
 if(timer){clearInterval(timer);timer=null;aud.pause();document.getElementById('play').textContent='play';return;}
 document.getElementById('play').textContent='stop';t=0;aud.currentTime=0;aud.play();
 timer=setInterval(()=>{draw(ROWS[t]);t++;if(t>=ROWS.length){clearInterval(timer);timer=null;document.getElementById('play').textContent='play';}},MS);
};
</script>"""
    html = (html.replace("__WAV__", wav_b64)
                .replace("__ROWS__", str(data))
                .replace("__MS__", str(ms))
                .replace("__N__", str(n)))
    Path(path).write_text(html)


def main():
    import random

    p = argparse.ArgumentParser(description="Preview the creature's strip and voice from field state.")
    p.add_argument("--scenario", choices=["day", "bursts", "quiet"], default="day")
    p.add_argument("--ticks", type=int, default=360)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--pixels", type=int, default=KNOBS["PIXELS"])
    p.add_argument("--secs-per-tick", type=float, default=0.09, help="audio/animation seconds per field tick")
    p.add_argument("--sr", type=int, default=22050)
    p.add_argument("--out", default="tools/preview", help="output directory")
    args = p.parse_args()

    knobs = dict(KNOBS, PIXELS=args.pixels)
    np.random.seed(args.seed)
    rng = random.Random(args.seed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    field = build_field()
    decoder = ExpressionDecoderV06(
        pixels=args.pixels,
        knobs={"LED_CAP": knobs["LED_CAP"]},
    )

    rows, A, B, T, events = [], [], [], [], []
    for senses in scenario_inputs(args.scenario, args.ticks, rng):
        state = field.step(senses)
        sig = decoder.read(state)
        rows.append(sig["pixels"])
        A.append(sig["A"]); B.append(sig["B"]); T.append(sig["T"])
        events.append(sig["event"])

    png = out_dir / "preview.png"
    wav = out_dir / "voice.wav"
    html = out_dir / "player.html"

    render_png(png, rows, A, B, T, args.scenario)
    samples = synth_voice(A, B, T, events, args.secs_per_tick, args.sr, knobs)
    write_wav(wav, samples, args.sr)
    write_html(html, rows, wav, args.secs_per_tick)

    print(f"scenario={args.scenario} ticks={args.ticks} pixels={args.pixels}")
    print(f"arousal  mean={np.mean(A):.3f} max={np.max(A):.3f}")
    print(f"balance  mean={np.mean(B):+.3f} (>0 warm/sound, <0 cool/light)")
    print(f"tempo    mean={np.mean(T):.3f} max={np.max(T):.3f}  events={sum(events)}")
    print(f"wrote {png}")
    print(f"wrote {wav}  ({len(samples)/args.sr:.1f}s)")
    print(f"wrote {html}")


if __name__ == "__main__":
    main()
