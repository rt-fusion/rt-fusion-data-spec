# RT-Fusion Data Format Specification

Data format, metadata schema and reference tools for egocentric data captured by [RT-Fusion](https://rt-fusion.com).

Each capture pairs two cameras recording in parallel:

| Stream | Device | Records |
|---|---|---|
| World | GoPro HERO13 Black, chest-mounted (windshield-mounted for driving) | 4K or 5.3K video in 10-bit GP-Log, with accelerometer and gyroscope (~200 Hz) and GPS (10 Hz) embedded as GPMF telemetry |
| POV | Ray-Ban Meta Gen 2 glasses | Head-locked first-person video at eye level, 3K or 1080p portrait at 30 fps, clips of up to 3 minutes |

The POV stream is a head-pose proxy: it shows what is in front of the wearer's head. **The rig has no eye tracking.**

The cameras are not hardware-synchronized. Each POV clip is placed on the world camera's timeline after capture by matching the two audio tracks, and its offset is stored in the scene's metadata.

## Contents

| Path | What it is |
|---|---|
| [SPEC.md](SPEC.md) | Full specification: sensors, delivery layout, time model, metadata fields, telemetry |
| [schema/scene-metadata.schema.json](schema/scene-metadata.schema.json) | JSON Schema for each scene's `metadata.json` |
| [examples/scene-metadata.example.json](examples/scene-metadata.example.json) | Example metadata file (illustrative values) |
| [tools/validate_metadata.py](tools/validate_metadata.py) | Checks `metadata.json` against the schema and the timeline rules |
| [tools/align_streams.py](tools/align_streams.py) | Finds where a POV clip starts on the world timeline from the two audio tracks |
| [tools/paired_frames.py](tools/paired_frames.py) | Time-aligned world/POV frame pairs, usable as a PyTorch `Dataset` |
| [docs/ros2-conversion.md](docs/ros2-conversion.md) | Topic layout and timestamps for converting a scene to a ROS 2 bag |

## Delivery layout

```
<scene_id>/
├── metadata.json
├── world/      GoPro chapter files as recorded, with GPMF telemetry embedded
└── pov/        Ray-Ban Meta clips as recorded
```

## Quick start

Requires Python 3.9+, and ffmpeg on PATH for `align_streams.py`.

```bash
pip install -r tools/requirements.txt

# Check a scene's metadata and that every referenced file is present
python tools/validate_metadata.py path/to/scene/metadata.json --check-files

# Estimate where a POV clip starts on the world timeline
python tools/align_streams.py path/to/scene/world/GX010492.MP4 path/to/scene/pov/RM010492_01.mp4

# Count time-aligned pairs and save side-by-side previews to check alignment by eye
python tools/paired_frames.py path/to/scene --stride 0.5 --preview 5 --out previews/
```

In Python, from the repository root:

```python
from tools.paired_frames import PairedFrames

pairs = PairedFrames("path/to/scene", stride_s=0.5, world_size=(960, 540))
sample = pairs[0]   # {"t": seconds on the world timeline, "world": frame, "pov": frame}
```

With PyTorch installed, frames are float tensors (C × H × W, values 0 to 1) and `PairedFrames` works with `torch.utils.data.DataLoader`. Without it, frames are RGB numpy arrays.

## Data protection

Footage is delivered raw by default and may show faces and license plates of people in public spaces. For each project, data protection roles (controller / processor), anonymization requirements and retention are agreed in a written data processing agreement. Wearers give written consent before capture.

## Contact

Arty Zuev · rt@rt-fusion.com · [rt-fusion.com](https://rt-fusion.com)

## License

MIT. See [LICENSE](LICENSE).
