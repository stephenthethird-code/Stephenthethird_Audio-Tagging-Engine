#!/usr/bin/env python3
"""
Generate a small demo library of synthetic audio (no copyrighted music) so you can try the whole app:

    python3 scripts/make_demo_library.py demo_audio 50
    # then in the app: Import folder -> demo_audio

Styles differ in tempo, rhythm, brightness and key, so sound matching, moods and genre suggestions have something to find.
Includes re-exports of the same track (same audio under two names) and same-title versions to demo version stacks.
Needs numpy. Writes ~12 s mono 22.05 kHz WAVs (~0.5 MB each).
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 22050
rng = np.random.RandomState(7)
NOTES = {"A": 220.0, "C": 261.63, "D": 293.66, "E": 329.63, "F": 174.61, "G": 196.0}


def env(n, attack=0.005, decay=0.15):
    t = np.arange(n) / SR
    return np.minimum(t / attack, 1) * np.exp(-t / decay)


def kick(n=int(0.25 * SR)):
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * (45 + 110 * np.exp(-t * 30)) * t) * np.exp(-t * 11)


def hat(n=int(0.05 * SR)):
    return rng.randn(n) * np.exp(-np.arange(n) / SR * 90) * 0.35


def render(style, bpm, root, secs=12):
    n = int(secs * SR); y = np.zeros(n); beat = 60.0 / bpm; f0 = NOTES[root]
    minor = style in ("trap", "drill", "rnb")
    def put(sig, at, gain=1.0):
        i = int(at * SR)
        if i < n: y[i:i + len(sig[: n - i])] += sig[: n - i] * gain
    t = 0.0; step = 0
    while t < secs:
        if style == "house":
            put(kick(), t, 0.9); put(hat(), t + beat / 2, 0.8)
        elif style in ("trap", "drill"):
            if step % 8 in (0, 5): put(kick(int(0.4 * SR)), t, 1.0)
            for h in range(4 if style == "trap" else 3): put(hat(), t + h * beat / 4, 0.7)
        elif style == "rnb":
            if step % 4 in (0, 2): put(kick(), t, 0.5)
        elif style == "lofi":
            if step % 4 == 0: put(kick(), t, 0.5)
            put(hat(int(0.03 * SR)), t + beat / 2, 0.4)
        t += beat; step += 1
    tt = np.arange(n) / SR
    third = 6 / 5 if minor else 5 / 4
    if style in ("rnb", "lofi"):                       # soft pads
        for mult in (1, third, 1.5):
            y += 0.12 * np.sin(2 * np.pi * f0 * mult * tt) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.2 * tt))
    else:                                              # bass
        y += 0.28 * np.sin(2 * np.pi * (f0 / 2) * tt) * (0.6 + 0.4 * (np.sin(2 * np.pi * (1 / beat) * tt) > 0))
        if style != "house": y += 0.05 * np.sin(2 * np.pi * f0 * 4 * tt)
    if style == "lofi": y *= 0.7; y += rng.randn(n) * 0.01
    return np.clip(y / max(1e-6, np.abs(y).max()) * 0.8, -1, 1)


def write(path: Path, y):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((y * 32000).astype("<i2").tobytes())


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_audio"); total = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    plan = {"trap": (130, 160), "drill": (138, 146), "house": (120, 128), "rnb": (70, 96), "lofi": (72, 88)}
    titles = ["midnight drive", "glass city", "paper planes", "neon rain", "slow burn", "open road", "night shift", "golden hour", "static bloom",
              "low tide", "afterglow", "blue hour", "city lights", "summer haze", "last train", "velvet sky", "iron lung", "quiet storm",
              "heat wave", "morning fog", "red eye", "sidewalk", "tin roof", "cold brew", "pale moon"]
    made, i = 0, 0
    while made < total:
        style = list(plan)[i % len(plan)]; lo, hi = plan[style]; bpm = int(rng.randint(lo, hi + 1)); root = list(NOTES)[rng.randint(len(NOTES))]
        title = titles[i % len(titles)] + ("" if i < len(titles) else f" {i // len(titles) + 1}")
        y = render(style, bpm, root); year = 2022 + i % 4; kind = "beats" if i % 3 else "samples"
        base = out / kind.upper() / str(year) / f"{title} {style} {bpm}bpm.wav"
        write(base, y); made += 1
        if i % 7 == 0 and made < total:                   # same audio re-exported under another name -> 'same audio' stack
            write(out / kind.upper() / str(year) / f"{title} {style} {bpm}bpm FINAL.wav", y); made += 1
        if i % 7 == 3 and made < total:                   # same title, different mix -> 'same title' stack
            write(out / kind.upper() / str(year) / f"{title} {style} v2 {bpm}bpm.wav", render(style, bpm, root, 12) * 0.9 + rng.randn(len(y)) * 0.004); made += 1
        i += 1
    print(f"Wrote {made} files to {out}/")


if __name__ == "__main__":
    main()
