#!/usr/bin/env python3
"""Robust f0 statistics for a wav, used to keep one unit's voice at one pitch.

Autocorrelation with octave-error rejection: the winning lag is the *first* peak
whose normalised correlation is within 12% of the global best, which stops the
tracker from dropping an octave on the second harmonic.

CLI:
    python3 tools/pitch_stats.py a.wav b.wav ...
"""

import sys
import wave

import numpy as np


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        ch = w.getnchannels()
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def f0_track(path, fmin=55.0, fmax=420.0, frame=0.045, hop=0.015, voicing=0.35):
    a, sr = read_wav(path)
    n, h = int(frame * sr), int(hop * sr)
    out = []
    for i in range(0, max(0, len(a) - n), h):
        x = a[i:i + n]
        if np.sqrt((x ** 2).mean()) < 0.008:
            continue
        x = x - x.mean()
        nrg = np.dot(x, x)
        if nrg <= 0:
            continue
        c = np.correlate(x, x, "full")[n - 1:]
        lo, hi = int(sr / fmax), min(int(sr / fmin), len(c) - 1)
        if hi <= lo + 2:
            continue
        seg = c[lo:hi] / nrg
        best = seg.max()
        if best < voicing:
            continue
        peaks = [j for j in range(1, len(seg) - 1)
                 if seg[j] >= seg[j - 1] and seg[j] >= seg[j + 1]]
        if not peaks:
            continue
        strong = [j for j in peaks if seg[j] >= 0.88 * best]
        j = strong[0] if strong else peaks[int(np.argmax([seg[p] for p in peaks]))]
        lag = lo + j
        y0, y1, y2 = seg[j - 1], seg[j], seg[j + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            lag += 0.5 * (y0 - y2) / den
        f = sr / lag
        if fmin <= f <= fmax:
            out.append(f)
    return np.array(out, dtype=np.float32)


def praat_f0(path, fmin=55.0, fmax=350.0):
    """Praat autocorrelation f0 - far fewer octave errors than the plain tracker.

    Falls back to f0_track() when parselmouth is not installed.
    """
    try:
        import parselmouth
    except Exception:
        return f0_track(path, fmin=fmin, fmax=fmax)
    sound = parselmouth.Sound(path)
    pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=fmin, pitch_ceiling=fmax,
                              octave_jump_cost=2.0)
    f = pitch.selected_array["frequency"]
    return np.asarray(f[f > 0], dtype=np.float32)


def pitch_mode(path, fmin=55.0, fmax=350.0, bw=0.06):
    """The register of a line: peak of a log-domain density of its f0 frames.

    A median drifts with long falling tails at the end of a sentence, which made
    an earlier version pitch-shift lines that were already in the right place.
    The mode is what a listener hears as "the pitch of that voice".
    """
    f = praat_f0(path, fmin, fmax)
    if len(f) < 8:
        return 0.0
    l = np.log2(f)
    grid = np.linspace(l.min(), l.max(), 200)
    dens = np.exp(-0.5 * ((grid[:, None] - l[None, :]) / bw) ** 2).sum(axis=1)
    return float(2.0 ** grid[dens.argmax()])


def register(path, fmin=55.0, fmax=350.0, bw=0.06):
    """Register of a clip plus how much it drifts inside itself.

    Returns dict(mode, drift, n).  `drift` is the distance in semitones between the
    median of the first and the second half of the voiced frames: a take that drifts
    by more than a couple of semitones is the model sliding into its other register
    mid-line, which is exactly what made one line sound like two voices.
    """
    try:
        import parselmouth
    except Exception:
        return dict(mode=0.0, drift=0.0, n=0)
    try:
        pitch = parselmouth.Sound(path).to_pitch_ac(
            time_step=0.01, pitch_floor=fmin, pitch_ceiling=fmax, octave_jump_cost=2.0)
        f = pitch.selected_array["frequency"]
        f = np.asarray(f[f > 0], dtype=np.float32)
    except Exception:
        return dict(mode=0.0, drift=0.0, n=0)
    if len(f) < 8:
        return dict(mode=0.0, drift=0.0, n=int(len(f)))
    l = np.log2(f)
    grid = np.linspace(l.min(), l.max(), 200)
    dens = np.exp(-0.5 * ((grid[:, None] - l[None, :]) / bw) ** 2).sum(axis=1)
    mode = float(2.0 ** grid[dens.argmax()])
    half = len(f) // 2
    a, b = float(np.median(f[:half])), float(np.median(f[half:]))
    drift = abs(12.0 * np.log2(b / a)) if a > 0 and b > 0 else 0.0
    return dict(mode=mode, drift=float(drift), n=int(len(f)))


def summary(path):
    f = f0_track(path)
    if len(f) < 5:
        return None
    return dict(median=float(np.median(f)), p25=float(np.percentile(f, 25)),
                p75=float(np.percentile(f, 75)), n=int(len(f)), semitones=float(12 * np.log2(np.median(f) / 100.0)))


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for p in argv:
        s = summary(p)
        if s is None:
            print("%-46s (no voiced frames)" % p.split("/")[-1])
            continue
        print("%-46s med %6.1f Hz  p25 %6.1f  p75 %6.1f  frames %4d"
              % (p.split("/")[-1], s["median"], s["p25"], s["p75"], s["n"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
