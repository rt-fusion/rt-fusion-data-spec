#!/usr/bin/env python3
"""Validate RT-Fusion scene metadata (metadata.json).

Runs two kinds of checks:
  1. Schema: the file matches schema/scene-metadata.schema.json.
  2. Consistency: world chapters line up on the timeline and every POV
     clip falls inside the world recording.

Usage:
    python tools/validate_metadata.py SCENE/metadata.json [MORE.json ...]
    python tools/validate_metadata.py SCENE/metadata.json --check-files

Exit code is 1 if any file has schema errors, otherwise 0.
Consistency problems are reported as warnings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "scene-metadata.schema.json"
TOLERANCE_S = 0.1  # allowed gap/overlap between chapters, and POV overhang


def consistency_warnings(meta: dict) -> list[str]:
    """Timeline checks that a JSON schema cannot express."""
    warnings: list[str] = []
    world = sorted(meta["files"]["world"], key=lambda f: f["start_s"])

    if world[0]["start_s"] > TOLERANCE_S:
        warnings.append(f"first world file starts at {world[0]['start_s']} s; expected 0")

    for prev, cur in zip(world, world[1:]):
        expected = prev["start_s"] + prev["duration_s"]
        gap = cur["start_s"] - expected
        if abs(gap) > TOLERANCE_S:
            kind = "gap" if gap > 0 else "overlap"
            warnings.append(
                f"{kind} of {abs(gap):.3f} s between {prev['path']} and {cur['path']}"
            )

    world_end = world[-1]["start_s"] + world[-1]["duration_s"]
    for clip in meta["files"]["pov"]:
        start = clip["offset_s"]
        end = start + clip["duration_s"]
        if start < -TOLERANCE_S or end > world_end + TOLERANCE_S:
            warnings.append(
                f"{clip['path']} spans {start:.3f}-{end:.3f} s, outside the world "
                f"recording (0-{world_end:.3f} s)"
            )
        alignment = clip.get("alignment", {})
        ratio = alignment.get("peak_ratio")
        if alignment.get("method") == "audio_xcorr" and ratio is not None and ratio < 2.0:
            warnings.append(f"{clip['path']}: weak audio match (peak_ratio={ratio}); check by eye")
    return warnings


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_problems(meta: dict, scene_dir: Path, verify_hashes: bool) -> list[str]:
    problems: list[str] = []
    for entry in meta["files"]["world"] + meta["files"]["pov"]:
        path = scene_dir / entry["path"]
        if not path.is_file():
            problems.append(f"missing file: {entry['path']}")
        elif verify_hashes and "sha256" in entry and sha256_of(path) != entry["sha256"]:
            problems.append(f"checksum mismatch: {entry['path']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("metadata", nargs="+", type=Path, help="metadata.json file(s)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="schema file (default: repo schema)")
    parser.add_argument("--check-files", action="store_true", help="check that referenced video files exist")
    parser.add_argument("--verify-hashes", action="store_true", help="with --check-files, also verify sha256 values (slow)")
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    failed = False
    for meta_path in args.metadata:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL {meta_path}: cannot read JSON ({exc})")
            failed = True
            continue

        errors = sorted(validator.iter_errors(meta), key=lambda e: list(e.absolute_path))
        if errors:
            failed = True
            print(f"FAIL {meta_path}: {len(errors)} schema error(s)")
            for error in errors:
                location = "/".join(str(part) for part in error.absolute_path) or "(root)"
                print(f"  - {location}: {error.message}")
            continue

        warnings = consistency_warnings(meta)
        if args.check_files:
            warnings += file_problems(meta, meta_path.parent, args.verify_hashes)

        status = "OK  " if not warnings else "WARN"
        print(f"{status} {meta_path}")
        for warning in warnings:
            print(f"  - {warning}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
