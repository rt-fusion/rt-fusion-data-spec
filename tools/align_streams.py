#!/usr/bin/env python3
"""Estimate where a POV clip starts on the world-camera timeline.

The world camera (GoPro) and the POV camera (Ray-Ban Meta) are not
hardware-synchronized. Both record audio, so this tool matches the two
audio tracks: it builds an onset envelope for each (sharp sounds such as
traffic, footsteps or wind gusts), cross-correlates them and reports the
time, in seconds, at which the POV clip's first frame was recorded on the
world file's timeline.

Requires ffmpeg and ffprobe on PATH, and numpy.

Examples:
    # POV clip started shortly after the world camera (searches the first 120 s)
    python tools/align_streams.py world/GX010492.MP4 pov/RM010492_01.mp4

    # POV clip started around minute 7 of the world file
    python tools/align_streams.py world/GX010492.MP4 pov/RM010492_02.mp4 --near 420

    # Machine-readable output
    python tools/align_streams.py WORLD.MP4 POV.mp4 --json

If the world recording is split into chapters (GX01..., GX02...), pass the
chapter in which the POV clip starts and add that chapter's start_s from
metadata.json to the result.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

AUDIO_RATE = 8000      # Hz, audio is decoded to mono at this rate
ENVELOPE_RATE = 1000   # Hz, onset envelope resolution (1 ms)
SMOOTH_MS = 40         # envelope smoothing; wider is more robust to noise, the peak stays sharp
EXCLUDE_MS = 100       # zone around the main peak ignored when finding the runner-up
MIN_PEAK_RATIO = 2.0   # main peak must be this many times the runner-up, else "weak"


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"error: {tool} not found on PATH")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        sys.exit(f"error: cannot read duration of {path}: {result.stderr.strip()}")


def read_audio(path: Path, start_s: float, duration_s: float) -> np.ndarray:
    """Decode a section of the audio track as mono float32 at AUDIO_RATE."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(path), "-t", f"{duration_s:.3f}", "-vn", "-ac", "1",
            "-ar", str(AUDIO_RATE), "-f", "f32le", "pipe:1"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        sys.exit(f"error: ffmpeg could not decode audio from {path}: "
                 f"{result.stderr.decode(errors='replace').strip()[-400:]}")
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float64)
    if samples.size < AUDIO_RATE:
        sys.exit(f"error: {path} has no usable audio in the requested range")
    return samples


def onset_envelope(samples: np.ndarray) -> np.ndarray:
    """Gain-independent onset strength at ENVELOPE_RATE.

    Pre-emphasis removes rumble and DC, the log makes the result independent
    of microphone gain, and the positive difference keeps sound onsets, which
    both microphones hear at the same moment.
    """
    emphasized = np.empty_like(samples)
    emphasized[0] = samples[0]
    emphasized[1:] = samples[1:] - 0.97 * samples[:-1]

    hop = AUDIO_RATE // ENVELOPE_RATE
    frames = len(emphasized) // hop
    envelope = np.abs(emphasized[: frames * hop]).reshape(frames, hop).mean(axis=1)

    kernel = np.ones(SMOOTH_MS) / SMOOTH_MS
    envelope = np.convolve(envelope, kernel, mode="same")

    floor = 1e-3 * np.percentile(envelope, 99) + 1e-12
    log_envelope = np.log(envelope + floor)
    onset = np.diff(log_envelope, prepend=log_envelope[0])
    return np.maximum(onset, 0.0)


def normalized_xcorr(reference: np.ndarray, probe: np.ndarray) -> np.ndarray:
    """Normalized cross-correlation of probe against every position in reference."""
    n, m = len(reference), len(probe)
    probe = probe - probe.mean()
    probe_norm = np.linalg.norm(probe)
    if probe_norm == 0:
        return np.zeros(n - m + 1)

    size = 1 << int(np.ceil(np.log2(n + m)))
    spectrum = np.fft.rfft(reference, size) * np.conj(np.fft.rfft(probe, size))
    raw = np.fft.irfft(spectrum, size)[: n - m + 1]

    cumsum = np.concatenate(([0.0], np.cumsum(reference)))
    cumsum_sq = np.concatenate(([0.0], np.cumsum(reference ** 2)))
    window_sum = cumsum[m:] - cumsum[:-m]
    window_sq = cumsum_sq[m:] - cumsum_sq[:-m]
    window_var = np.maximum(window_sq - window_sum ** 2 / m, 1e-12)
    return raw / (np.sqrt(window_var) * probe_norm)


def align(world: Path, pov: Path, near_s: float, window_s: float, pov_seconds: float) -> dict:
    world_duration = media_duration(world)
    pov_duration = media_duration(pov)
    probe_s = min(pov_seconds, pov_duration)
    if near_s >= world_duration:
        sys.exit(f"error: --near {near_s} s is past the end of the world file ({world_duration:.1f} s)")

    search_start = max(0.0, near_s - window_s)
    search_end = min(world_duration, near_s + window_s + probe_s)
    if search_end - search_start <= probe_s:
        sys.exit("error: search range is shorter than the POV audio; check --near/--window")

    world_env = onset_envelope(read_audio(world, search_start, search_end - search_start))
    pov_env = onset_envelope(read_audio(pov, 0.0, probe_s))
    if len(world_env) <= len(pov_env):
        sys.exit("error: not enough world audio in the search range")

    ncc = normalized_xcorr(world_env, pov_env)
    peak = int(np.argmax(ncc))

    fraction = 0.0
    if 0 < peak < len(ncc) - 1:
        left, centre, right = ncc[peak - 1], ncc[peak], ncc[peak + 1]
        curvature = left - 2 * centre + right
        if curvature < 0:
            fraction = 0.5 * (left - right) / curvature

    exclude = int(EXCLUDE_MS * ENVELOPE_RATE / 1000)
    others = np.concatenate((ncc[: max(0, peak - exclude)], ncc[peak + exclude + 1:]))
    runner_up = float(others.max()) if others.size else 0.0
    peak_value = float(ncc[peak])
    peak_ratio = peak_value / runner_up if runner_up > 0 else float("inf")

    offset_s = search_start + (peak + fraction) / ENVELOPE_RATE
    # In noisy audio the absolute correlation stays low even for a correct match,
    # so reliability is judged by how clearly the peak stands out.
    weak = peak_ratio < MIN_PEAK_RATIO
    return {
        "world": str(world),
        "pov": str(pov),
        "offset_s": round(offset_s, 4),
        "ncc": round(peak_value, 3),
        "peak_ratio": round(peak_ratio, 2) if np.isfinite(peak_ratio) else None,
        "quality": "weak" if weak else "good",
        "searched_s": [round(search_start, 3), round(search_end - probe_s, 3)],
        "pov_audio_used_s": round(probe_s, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("world", type=Path, help="world-camera video (GoPro file)")
    parser.add_argument("pov", type=Path, help="POV clip (Ray-Ban Meta file)")
    parser.add_argument("--near", type=float, default=0.0,
                        help="approximate POV start on the world file timeline, in seconds (default 0)")
    parser.add_argument("--window", type=float, default=120.0,
                        help="search this many seconds either side of --near (default 120)")
    parser.add_argument("--pov-seconds", type=float, default=60.0,
                        help="how much POV audio to match, from the start of the clip (default 60)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON only")
    args = parser.parse_args()

    require_tools()
    for path in (args.world, args.pov):
        if not path.is_file():
            sys.exit(f"error: file not found: {path}")

    result = align(args.world, args.pov, args.near, args.window, args.pov_seconds)

    if args.json:
        print(json.dumps(result))
    else:
        print(f"POV clip starts at {result['offset_s']:.3f} s on the world file timeline")
        print(f"match: ncc={result['ncc']}, peak_ratio={result['peak_ratio']} ({result['quality']})")
        if result["quality"] == "weak":
            print("weak match: check this clip by eye, or retry with --near closer to the real start")
    return 0 if result["quality"] == "good" else 2


if __name__ == "__main__":
    sys.exit(main())
