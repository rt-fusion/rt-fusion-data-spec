#!/usr/bin/env python3
"""Time-aligned world/POV frame pairs from an RT-Fusion scene.

Reads a scene folder (metadata.json, world/, pov/) and, for sample times on
the world timeline, returns the world frame and the POV frame recorded at
the same moment, using each POV clip's offset_s from metadata.json.

Works as a PyTorch Dataset when torch is installed (frames become float
tensors, C x H x W, values 0-1); otherwise frames are RGB numpy arrays.

Python:
    from tools.paired_frames import PairedFrames
    pairs = PairedFrames("path/to/scene", stride_s=0.5, world_size=(960, 540))
    sample = pairs[0]          # {"t": 1.784, "world": ..., "pov": ...}

Command line (count pairs, optionally save side-by-side previews to check alignment):
    python tools/paired_frames.py path/to/scene --stride 0.5 --preview 5 --out previews/

Frames are fetched by seeking, which is fine for sampling. For frame-exact
work on long-GOP video, decode sequentially or pre-extract frames with ffmpeg.
IMU and GPS stay in the GoPro files as GPMF; see SPEC.md for how to read them
and place them on the same world timeline.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

try:
    import torch
    from torch.utils.data import Dataset
except ImportError:  # the index and numpy frames work without PyTorch
    torch = None
    Dataset = object

EDGE_MARGIN_S = 0.05  # keep samples this far from file boundaries


@dataclass(frozen=True)
class Sample:
    t: float            # world timeline, seconds
    world_path: Path
    world_t: float      # time inside the world file
    pov_path: Path
    pov_t: float        # time inside the POV clip


def build_index(scene_dir: Path, stride_s: float) -> tuple[dict, list[Sample]]:
    """List sample times where both streams have footage."""
    meta = json.loads((scene_dir / "metadata.json").read_text(encoding="utf-8"))
    world_files = sorted(meta["files"]["world"], key=lambda f: f["start_s"])

    samples: list[Sample] = []
    for clip in sorted(meta["files"]["pov"], key=lambda c: c["offset_s"]):
        first = clip["offset_s"] + EDGE_MARGIN_S
        last = clip["offset_s"] + clip["duration_s"] - EDGE_MARGIN_S
        if last < first:
            continue
        for k in range(int(math.floor((last - first) / stride_s)) + 1):
            t = first + k * stride_s
            for chapter in world_files:
                start = chapter["start_s"]
                if start + EDGE_MARGIN_S <= t <= start + chapter["duration_s"] - EDGE_MARGIN_S:
                    samples.append(Sample(
                        t=round(t, 6),
                        world_path=scene_dir / chapter["path"],
                        world_t=t - start,
                        pov_path=scene_dir / clip["path"],
                        pov_t=t - clip["offset_s"],
                    ))
                    break
    return meta, samples


class PairedFrames(Dataset):
    """World/POV frame pairs at a fixed stride on the world timeline."""

    def __init__(self, scene_dir, stride_s: float = 0.5,
                 world_size: tuple[int, int] | None = None,
                 pov_size: tuple[int, int] | None = None):
        if stride_s <= 0:
            raise ValueError("stride_s must be positive")
        self.scene_dir = Path(scene_dir)
        self.meta, self.samples = build_index(self.scene_dir, stride_s)
        self.world_size = world_size   # (width, height) or None to keep native size
        self.pov_size = pov_size
        self._captures: dict[Path, cv2.VideoCapture] = {}

    def __len__(self) -> int:
        return len(self.samples)

    def __getstate__(self):
        # Video handles cannot be pickled (DataLoader workers on Windows/macOS);
        # each worker opens its own.
        state = self.__dict__.copy()
        state["_captures"] = {}
        return state

    def _frame(self, path: Path, t: float, size):
        capture = self._captures.get(path)
        if capture is None:
            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                raise RuntimeError(f"cannot open {path}")
            self._captures[path] = capture
        capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"cannot read frame at {t:.3f} s from {path}")
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if size is not None:
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        return frame

    def __getitem__(self, index: int) -> dict:
        sample = self.samples[index]
        world = self._frame(sample.world_path, sample.world_t, self.world_size)
        pov = self._frame(sample.pov_path, sample.pov_t, self.pov_size)
        if torch is not None:
            world = torch.from_numpy(world).permute(2, 0, 1).float().div_(255)
            pov = torch.from_numpy(pov).permute(2, 0, 1).float().div_(255)
        return {"t": sample.t, "world": world, "pov": pov}

    def close(self) -> None:
        for capture in self._captures.values():
            capture.release()
        self._captures.clear()


def side_by_side(world: np.ndarray, pov: np.ndarray, height: int = 480) -> np.ndarray:
    """World and POV frames next to each other at the same height (RGB)."""
    def fit(image):
        scale = height / image.shape[0]
        return cv2.resize(image, (max(1, round(image.shape[1] * scale)), height),
                          interpolation=cv2.INTER_AREA)
    return np.hstack([fit(world), fit(pov)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scene", type=Path, help="scene folder containing metadata.json")
    parser.add_argument("--stride", type=float, default=0.5, help="seconds between samples (default 0.5)")
    parser.add_argument("--preview", type=int, default=0, help="save this many side-by-side preview images")
    parser.add_argument("--out", type=Path, default=Path("previews"), help="folder for preview images")
    args = parser.parse_args()

    pairs = PairedFrames(args.scene, stride_s=args.stride)
    print(f"{len(pairs)} paired samples at {args.stride} s stride in {args.scene}")

    if args.preview > 0 and len(pairs) > 0:
        args.out.mkdir(parents=True, exist_ok=True)
        count = min(args.preview, len(pairs))
        picks = np.linspace(0, len(pairs) - 1, count).round().astype(int)
        for index in picks:
            sample = pairs.samples[index]
            world = pairs._frame(sample.world_path, sample.world_t, None)
            pov = pairs._frame(sample.pov_path, sample.pov_t, None)
            image = cv2.cvtColor(side_by_side(world, pov), cv2.COLOR_RGB2BGR)
            target = args.out / f"pair_{sample.t:09.3f}s.jpg"
            cv2.imwrite(str(target), image)
            print(f"saved {target}")
    pairs.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
