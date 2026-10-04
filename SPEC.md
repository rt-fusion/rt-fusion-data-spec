# RT-Fusion Data Format Specification

Version 0.1.0 · October 2026

This document defines how RT-Fusion delivers egocentric capture data: the sensors, the folder layout, the time model that links the two video streams, the per-scene metadata file and the telemetry.

## 1. Sensors

Every capture uses two cameras recording in parallel.

| Stream | Device | Mount | Output |
|---|---|---|---|
| World | GoPro HERO13 Black | Chest (config A) or windshield / rigid (config B) | Continuous video with embedded GPMF telemetry: accelerometer and gyroscope (~200 Hz), GPS (10 Hz) |
| POV | Ray-Ban Meta Gen 2 | Head (glasses) | Head-locked first-person video at eye level, clips of up to 3 minutes |

The POV stream is a head-pose proxy: its center frame shows what is in front of the wearer's head. **The rig has no eye tracking.**

### Configurations

| Config | Stream | Optics | Resolution | Frame rate | Typical use |
|---|---|---|---|---|---|
| A | World | Ultra-wide lens mod, 177° FOV | 3840 × 2160 | 60 fps | Walking, cycling, indoor and outdoor navigation |
| B | World | Standard wide, ~113° FOV | 5312 × 2988 | 60 fps | Driving, long-range text, VIO testing |
| C1 | POV | Built-in | 2192 × 2928 (portrait) | 30 fps | High-resolution POV |
| C2 | POV | Built-in | 1456 × 1952 (portrait) | 30 fps | Low light, longer runs |

World video is recorded in 10-bit GP-Log. Neither stream is stabilized or color graded.

## 2. Delivery layout

One scene is one continuous world recording (all of its chapter files) plus every POV clip recorded during it.

```
<scene_id>/
├── metadata.json          scene metadata (section 4)
├── world/                 GoPro files, unedited
│   ├── GX010492.MP4       chapter 1
│   └── GX020492.MP4       chapter 2 (the GoPro splits long recordings into chapters)
└── pov/                   Ray-Ban Meta clips, unedited
    ├── RM010492_01.mp4
    └── RM010492_02.mp4
```

Video files are delivered as recorded: original container, codec and audio. Telemetry stays embedded in the GoPro files as GPMF. Checksums (SHA-256) can be listed per file in `metadata.json`.

## 3. Time model

**World timeline.** `t = 0` is the first frame of the first world chapter. Each chapter's `start_s` gives its position on this timeline. Chapters follow each other without gaps.

**POV clips.** Each clip's `offset_s` is the world-timeline time of its first frame. A POV frame at time `τ` inside its clip was recorded at world time `offset_s + τ`.

**How offsets are found.** The two cameras are not hardware-synchronized, and POV clips are started by hand, typically 1.5 to 2.0 s after the world camera. Offsets are estimated by matching the two audio tracks with `tools/align_streams.py` (section 6). The method and match quality are stored with each clip.

**UTC.** `capture.start_utc` is the UTC time of world `t = 0`.
- `start_utc_source: "gps"`: taken from GPS time in the GoPro telemetry. Precise.
- `start_utc_source: "camera_clock"`: taken from the camera's file creation time, used when there is no GPS fix (indoors, for example). The camera clock can be off by many seconds, so treat it as coarse.

Relative timing between the streams does not depend on the UTC source.

## 4. Scene metadata (`metadata.json`)

Each scene folder contains one `metadata.json`, validated by [`schema/scene-metadata.schema.json`](schema/scene-metadata.schema.json). See [`examples/scene-metadata.example.json`](examples/scene-metadata.example.json) for a complete example.

| Field | Required | Description |
|---|---|---|
| `schema_version` | yes | Version of this specification, e.g. `"0.1.0"` |
| `scene_id` | yes | Unique scene identifier |
| `scenario` | no | Scenario label, e.g. `vru_cyclist_urban`, `robotics_stairs_entry` |
| `description` | no | Short free-text summary |
| `capture.start_utc` | yes | UTC time of world `t = 0`, ISO 8601 |
| `capture.start_utc_source` | yes | `gps` or `camera_clock` (section 3) |
| `capture.country` | yes | ISO 3166-1 alpha-2 code, e.g. `NL` |
| `capture.locality` | no | City or area |
| `capture.mode` | yes | How the wearer moved: `foot`, `bicycle`, `car`, `other` |
| `capture.setting` | no | `urban`, `suburban`, `rural`, `highway`, `offroad`, `indoor`, `mixed`, `other` |
| `capture.lighting` | no | `daylight`, `dusk`, `night`, `artificial`, `mixed` |
| `capture.weather`, `capture.surface` | no | Free text |
| `rig.world` | yes | `device`, `config` (`A`/`B`), `mount`, `resolution` `[w, h]`, `fps`; optional `lens`, `fov_deg`, `color_profile`, `telemetry` (GPMF stream names) |
| `rig.pov` | yes | `device`, `config` (`C1`/`C2`), `resolution` `[w, h]`, `fps`, `eye_tracking` (always `false`) |
| `files.world[]` | yes | Chapter files in order: `path`, `start_s`, `duration_s`, optional `sha256` |
| `files.pov[]` | yes | POV clips: `path`, `offset_s`, `duration_s`, `alignment` (`method`: `audio_xcorr`, `imu_event` or `manual`; optional `ncc`, `peak_ratio`), optional `sha256` |
| `privacy.anonymized` | yes | `true` if faces and license plates were blurred before delivery |
| `privacy.wearer_consent` | yes | `true` if the wearer gave written consent before capture |
| `notes` | no | Free text |
| `custom` | no | Project-specific fields agreed with the client |

Paths are relative to the scene folder. Times are in seconds.

## 5. Telemetry

The GoPro writes telemetry as a GPMF track inside each world MP4.

| GPMF stream | Content | Units | Rate |
|---|---|---|---|
| `ACCL` | 3-axis accelerometer | m/s² | ~200 Hz |
| `GYRO` | 3-axis gyroscope | rad/s | ~200 Hz |
| `GPS9` | Latitude, longitude, altitude, 2D and 3D speed, GPS time, DOP, fix type | deg, m, m/s | 10 Hz |

- Telemetry timestamps share the video clock. A sample at time `τ` in chapter `c` sits at world time `c.start_s + τ`.
- Axis order differs between GoPro models. Read it from the stream's `ORIN` tag instead of assuming X, Y, Z.
- GPS is only available outdoors with a fix. `GPS9` time is what `start_utc_source: "gps"` refers to.
- Readers: GoPro's open-source [gpmf-parser](https://github.com/gopro/gpmf-parser) (C), and community libraries for Python and JavaScript built on the same format.

## 6. Stream alignment

`tools/align_streams.py` estimates where a POV clip starts on the world file's timeline:

1. Decodes both audio tracks to mono at 8 kHz with ffmpeg.
2. Builds an onset envelope for each at 1 ms resolution. It is independent of microphone gain and emphasizes sharp sounds (traffic, footsteps, impacts) that both microphones hear at the same moment.
3. Cross-correlates the POV envelope against the world envelope inside a search window and refines the peak to sub-millisecond resolution.
4. Reports `offset_s`, the peak correlation `ncc` and `peak_ratio` (the main peak divided by the strongest competing peak). A match with `peak_ratio` below 2.0 is reported as weak and should be checked by eye.

Exit codes: `0` good match, `2` weak match, `1` error.

By default the tool searches the first 120 s of the world file. For clips that start later in a long recording, pass `--near` with an approximate start time. For a world recording split into chapters, run it against the chapter in which the clip starts and add that chapter's `start_s`.

On synthetic tests with independent noise and a different frequency response on each track, the tool recovers the offset to within 1 ms. Real-world accuracy depends on the sounds both microphones pick up, so weak matches should be checked visually, for example with `tools/paired_frames.py --preview`.

## 7. Data protection

Footage is delivered raw by default and may show faces and license plates of people and vehicles in public spaces. For each project, data protection roles (controller / processor), anonymization requirements and retention are agreed in a written data processing agreement. Wearers give written consent before capture. `privacy` in `metadata.json` records the status of each scene.

## 8. Versioning

This is version 0.1.0. Versions before 1.0 may change the schema. `schema_version` in each `metadata.json` records which version the file follows. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
