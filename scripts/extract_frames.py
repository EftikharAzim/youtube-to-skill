#!/usr/bin/env python3
"""
extract_frames.py — download a YouTube video at readable resolution and extract
slide-change keyframes for the youtube-to-skill visual pass.

Requires: yt-dlp AND ffmpeg on PATH.

The agent later Reads these frames as images and transcribes slides, on-screen
code, tables, and diagrams into the generated chapter files.

Outputs (in --workdir/frames_<video_id>/):
  f0001.jpg, f0002.jpg, ...   — one frame per detected scene change
  frames.json                 — [{file, t_seconds, ts}] mapping frames to timestamps

Usage:
  python3 extract_frames.py URL [--workdir DIR] [--height 720]
                            [--scene 0.30] [--max-frames 60] [--keep-video] [--check]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def check_env():
    ok = True
    for tool, hint in (("yt-dlp", "brew install yt-dlp"), ("ffmpeg", "brew install ffmpeg")):
        path = shutil.which(tool)
        if path:
            print(f"{tool} : OK at {path}")
        else:
            print(f"{tool} : MISSING → install with: {hint}")
            ok = False
    sys.exit(0 if ok else 1)


def get_expected_duration(url: str) -> float | None:
    """Ask yt-dlp for the video's reported duration (seconds) without downloading."""
    p = subprocess.run(
        ["yt-dlp", "--no-playlist", "--no-warnings", "--print", "%(duration)s", url],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        return float(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def probe_duration(video: Path) -> float | None:
    """Read the actual duration (seconds) of a downloaded file via ffprobe."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def download_video(url: str, height: int, workdir: Path) -> Path:
    """Download video-only stream capped at `height` (no audio needed for frames).

    Verifies the download against yt-dlp's own reported duration and retries once
    on a truncated/corrupt result — long-lecture downloads over a real network
    intermittently truncate, which silently produces garbage/wrong-looking frames
    downstream if left unchecked (observed in production: a 66min video yielding
    6 frames of unrelated content).
    """
    expected = get_expected_duration(url)
    fmt = f"bv*[height<={height}]/b[height<={height}]/bv*/b"
    out_tpl = str(workdir / "video_%(id)s.%(ext)s")

    for attempt in (1, 2):
        p = subprocess.run(
            ["yt-dlp", "-f", fmt, "--no-playlist", "--no-warnings",
             "--retries", "5", "--fragment-retries", "5",
             "-o", out_tpl, "--print", "after_move:filepath", "--no-simulate", url],
            capture_output=True, text=True)
        if p.returncode != 0:
            if attempt == 1:
                continue
            die(f"video download failed:\n{p.stderr.strip()[-800:]}")
        path = Path(p.stdout.strip().splitlines()[-1])
        if not path.exists():
            if attempt == 1:
                continue
            die(f"yt-dlp reported {path} but it does not exist")

        actual = probe_duration(path)
        if expected and actual and actual < 0.8 * expected:
            print(f"WARNING: downloaded duration {actual:.0f}s is far short of expected "
                  f"{expected:.0f}s (attempt {attempt}) — likely a truncated download.",
                  file=sys.stderr)
            if attempt == 1:
                path.unlink(missing_ok=True)
                continue
            die(f"download truncated after retry: got {actual:.0f}s, expected ~{expected:.0f}s. "
                f"Re-run, or investigate network/yt-dlp — do NOT trust frames extracted from this file.")
        return path

    die("download failed after retry")


def detect_frames(video: Path, frames_dir: Path, scene: float) -> list:
    """Extract scene-change keyframes; return [(file, t_seconds)] from showinfo output."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", f"select='eq(n,0)+gt(scene,{scene})',showinfo",
         "-fps_mode", "vfr", "-q:v", "3", str(frames_dir / "f%04d.jpg")],
        capture_output=True, text=True)
    if p.returncode != 0:
        die(f"ffmpeg failed:\n{p.stderr.strip()[-800:]}")
    times = [float(m) for m in re.findall(r"pts_time:([0-9]+\.?[0-9]*)", p.stderr)]
    files = sorted(frames_dir.glob("f*.jpg"))
    if len(times) != len(files):
        die(f"ffmpeg wrote {len(files)} frames but showinfo reported {len(times)} timestamps — "
            f"frame-to-timestamp alignment cannot be trusted, so slide citations would be wrong. "
            f"Try a different --scene value, or report this with the video URL.")
    return list(zip(files, times))


def uniform_sample(video: Path, frames_dir: Path, duration: float, count: int) -> list:
    """Fallback: sample `count` frames evenly across the video's full duration.

    The scene-change filter (detect_frames) has a known failure mode on some
    streams where it fires a handful of times near frame 0 and then goes silent
    for the rest of a long video (observed on a 66min lecture: 6 frames, all in
    the first 6 seconds). When that happens, scene detection is silently useless
    rather than loudly broken, so callers must check coverage and fall back.
    """
    for f in frames_dir.glob("f*.jpg"):
        f.unlink()
    times = [round(duration * i / (count - 1), 1) for i in range(count)] if count > 1 else [0.0]
    files = []
    for i, t in enumerate(times, start=1):
        out = frames_dir / f"f{i:04d}.jpg"
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, text=True)
        if p.returncode == 0 and out.exists():
            files.append(out)
        else:
            times = times[:len(files)]
    return list(zip(files, times[:len(files)]))


def subsample(frames: list, max_frames: int) -> list:
    """Evenly subsample to max_frames, always keeping first and last; delete the rest."""
    if len(frames) <= max_frames:
        return frames
    n = len(frames)
    keep_idx = sorted({round(i * (n - 1) / (max_frames - 1)) for i in range(max_frames)})
    keep = [frames[i] for i in keep_idx]
    kept_files = {f for f, _ in keep}
    for f, _ in frames:
        if f not in kept_files:
            f.unlink(missing_ok=True)
    return keep


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="YouTube video URL (single video)")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--height", type=int, default=720,
                    help="max video height; 720 keeps on-screen code legible (default: 720)")
    ap.add_argument("--scene", type=float, default=0.30,
                    help="scene-change threshold 0..1; lower = more frames (default: 0.30)")
    ap.add_argument("--max-frames", type=int, default=60,
                    help="cap on extracted frames; evenly subsampled if exceeded (default: 60)")
    ap.add_argument("--keep-video", action="store_true",
                    help="keep the downloaded video file after extraction")
    ap.add_argument("--check", action="store_true", help="verify environment and exit")
    args = ap.parse_args()

    if args.check:
        check_env()
    if not args.url:
        ap.error("a video URL is required (or use --check)")
    for tool in ("yt-dlp", "ffmpeg"):
        if not shutil.which(tool):
            die(f"{tool} not found on PATH (run with --check for install hints)")

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / "yt_skill_work"
    workdir.mkdir(parents=True, exist_ok=True)

    print("Downloading video stream…", file=sys.stderr)
    video = download_video(args.url, args.height, workdir)
    vid_id = re.sub(r"^video_|\.[^.]+$", "", video.name)
    frames_dir = workdir / f"frames_{vid_id}"

    duration = probe_duration(video) or 0.0

    print("Detecting scene changes…", file=sys.stderr)
    frames = detect_frames(video, frames_dir, args.scene)
    total_detected = len(frames)
    fallback_used = False

    # Known ffmpeg failure mode: the scene filter fires a handful of times near
    # frame 0 then goes silent for the rest of a long video, leaving scene
    # detection silently useless rather than loudly broken. Detect that and
    # fall back to evenly-spaced sampling across the full duration instead.
    last_t = max((t for _, t in frames), default=0.0)
    coverage_ratio = (last_t / duration) if duration else 1.0
    too_sparse = duration > 300 and len(frames) < max(3, round(duration / 600))
    if duration > 60 and (too_sparse or coverage_ratio < 0.5):
        print(f"WARNING: scene detection only found {total_detected} frame(s) covering "
              f"{coverage_ratio:.0%} of the {duration:.0f}s video — falling back to uniform "
              f"time-sampling instead of trusting sparse/clustered scene-change output.",
              file=sys.stderr)
        target = min(args.max_frames, max(10, round(duration / 90)))
        frames = uniform_sample(video, frames_dir, duration, target)
        total_detected = len(frames)
        fallback_used = True

    frames = subsample(frames, args.max_frames)

    index = [{"file": str(f), "t_seconds": round(t, 1), "ts": fmt_ts(t)} for f, t in frames]
    (frames_dir / "frames.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    if not args.keep_video:
        video.unlink(missing_ok=True)

    print(json.dumps({"frames_dir": str(frames_dir), "frames": len(index),
                      "detected": total_detected, "fallback_uniform_sampling": fallback_used,
                      "index": str(frames_dir / "frames.json")}))


if __name__ == "__main__":
    main()
