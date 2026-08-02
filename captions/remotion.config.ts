import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);

// VideoToolbox h264 encoding on Apple Silicon: ~15% off the wall clock and half the CPU
// time, which keeps the machine responsive while a clip renders. Hardware encoders ignore
// crf, so quality is pinned with a bitrate instead — 8 Mbps matches what crf 18 produced
// for these 1080x1920 clips.
Config.setHardwareAcceleration("if-possible");
Config.setVideoBitrate("8M");
