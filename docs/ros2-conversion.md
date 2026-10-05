# Converting a scene to ROS 2

Scenes are delivered as MP4 video, embedded GPMF telemetry and a `metadata.json` file (see [SPEC.md](../SPEC.md)). This page gives a recommended mapping to a rosbag2 file, so converted scenes share one layout.

## Topics

| Topic | Message type | Source | Rate |
|---|---|---|---|
| `/rtf/world/image/compressed` | `sensor_msgs/msg/CompressedImage` | Frames from `world/*.MP4` | World frame rate, or subsampled |
| `/rtf/pov/image/compressed` | `sensor_msgs/msg/CompressedImage` | Frames from `pov/*.mp4` | 30 fps, or subsampled |
| `/rtf/world/imu` | `sensor_msgs/msg/Imu` | GPMF `ACCL` + `GYRO` | ~200 Hz |
| `/rtf/world/fix` | `sensor_msgs/msg/NavSatFix` | GPMF `GPS9` | 10 Hz |

Frame IDs: `rtf_world_camera`, `rtf_pov_camera`, `rtf_world_imu`.

The IMU gives no orientation estimate, so set `orientation_covariance[0] = -1` as the `sensor_msgs/Imu` convention requires.

## Timestamps

Every message is stamped on one clock: `capture.start_utc` plus world-timeline time.

| Data | `header.stamp` |
|---|---|
| World frame at time `τ` in chapter `c` | `start_utc + c.start_s + τ` |
| POV frame at time `τ` in clip `p` | `start_utc + p.offset_s + τ` |
| IMU or GPS sample at time `τ` in chapter `c` | `start_utc + c.start_s + τ` |

If `start_utc_source` is `camera_clock`, absolute times are coarse, but timing between topics is still correct.

## Steps

1. Decode frames with ffmpeg or OpenCV and store them as JPEG (`format: "jpeg"`). The world video is 10-bit GP-Log; decoding to 8-bit keeps the log curve unless you apply a conversion.
2. Extract `ACCL`, `GYRO` and `GPS9` with a GPMF parser such as GoPro's [gpmf-parser](https://github.com/gopro/gpmf-parser). Map the axes using the stream's `ORIN` tag. Units are m/s² and rad/s.
3. Write the messages with `rosbag2_py` in a ROS 2 environment, or with the pure-Python [rosbags](https://pypi.org/project/rosbags/) library, which does not need a ROS installation.
