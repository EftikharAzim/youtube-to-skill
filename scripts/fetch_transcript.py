#!/usr/bin/env python3
"""
fetch_transcript.py — YouTube video(s)/playlist → clean timestamped transcript
for the youtube-to-skill converter.

Requires: yt-dlp on PATH (https://github.com/yt-dlp/yt-dlp)

Outputs (in --workdir, default <tempdir>/yt_skill_work):
  full_text.txt   — combined transcripts of all videos, with per-video headers,
                    YouTube chapter markers inlined as '## CHAPTER:' lines,
                    and [H:MM:SS] paragraph timestamps.
  metadata.json   — per-video metadata (title, channel, duration, chapters,
                    caption kind manual/auto, words) + combined totals.

Usage:
  python3 fetch_transcript.py URL [URL ...] [--lang en] [--workdir DIR]
                              [--max-videos N] [--check]
  python3 fetch_transcript.py --search "QUERY" [--max-results 8]
      → prints search results as JSON (no transcripts fetched); pick URLs,
        then run again with them.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PARA_GAP_S = 4.0      # start a new paragraph after a silence gap this long
PARA_MAX_S = 45.0     # or when the running paragraph exceeds this duration


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def check_env():
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print("yt-dlp : MISSING  → install with: brew install yt-dlp  (or: pipx install yt-dlp)")
        sys.exit(1)
    ver = run(["yt-dlp", "--version"]).stdout.strip()
    print(f"yt-dlp : OK ({ver}) at {ytdlp}")
    print("network: not tested — first real fetch will tell")


def search_videos(query: str, max_results: int) -> list:
    """YouTube search via yt-dlp's ytsearch pseudo-URL; returns result dicts."""
    p = run(["yt-dlp", "--flat-playlist", "-J", "--no-warnings",
             f"ytsearch{max_results}:{query}"])
    if p.returncode != 0:
        die(f"search failed:\n{p.stderr.strip()[-800:]}")
    entries = [e for e in (json.loads(p.stdout).get("entries") or []) if e]
    return [{
        "title": e.get("title"),
        "channel": e.get("channel") or e.get("uploader"),
        "duration": fmt_ts(e["duration"]) if e.get("duration") else None,
        "views": e.get("view_count"),
        "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
    } for e in entries]


def list_videos(url: str, max_videos: int) -> list:
    """Expand a URL (single video or playlist) into a list of video URLs."""
    p = run(["yt-dlp", "--flat-playlist", "-J", "--no-warnings", url])
    if p.returncode != 0:
        die(f"yt-dlp could not read {url}\n{p.stderr.strip()[-800:]}")
    info = json.loads(p.stdout)
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        urls = [e.get("url") or f"https://www.youtube.com/watch?v={e['id']}" for e in entries]
        if len(urls) > max_videos:
            print(f"NOTE: playlist has {len(urls)} videos; capping at {max_videos} "
                  f"(raise with --max-videos)", file=sys.stderr)
            urls = urls[:max_videos]
        return urls
    return [url]


def get_info(url: str) -> dict:
    p = run(["yt-dlp", "-J", "--no-playlist", "--no-warnings", url])
    if p.returncode != 0:
        die(f"yt-dlp could not fetch metadata for {url}\n{p.stderr.strip()[-800:]}")
    return json.loads(p.stdout)


def download_captions(url: str, video_id: str, lang: str, workdir: Path) -> tuple:
    """Download json3 captions. Returns (path, kind) where kind is 'manual' or 'auto'."""
    out_tpl = str(workdir / "%(id)s.%(ext)s")
    lang_spec = f"{lang}.*,{lang},-live_chat"

    # Try manual subtitles first, then auto-captions.
    for flag, kind in (("--write-subs", "manual"), ("--write-auto-subs", "auto")):
        run(["yt-dlp", "--skip-download", "--no-playlist", "--no-warnings", flag,
             "--sub-langs", lang_spec, "--sub-format", "json3", "-o", out_tpl, url])
        hits = sorted(workdir.glob(f"{video_id}.*.json3"))
        if hits:
            return hits[0], kind
    return None, None


def parse_json3(path: Path) -> list:
    """json3 → list of (start_seconds, text) caption events."""
    data = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            events.append((ev["tStartMs"] / 1000.0, text))
    return events


def events_to_paragraphs(events: list) -> list:
    """Group caption events into (start_ts, paragraph_text) chunks."""
    paras = []
    cur_start, cur_parts, last_t = None, [], None
    for t, text in events:
        if cur_start is None:
            cur_start, cur_parts = t, [text]
        elif (t - last_t) > PARA_GAP_S or (t - cur_start) > PARA_MAX_S:
            paras.append((cur_start, " ".join(cur_parts)))
            cur_start, cur_parts = t, [text]
        else:
            cur_parts.append(text)
        last_t = t
    if cur_parts:
        paras.append((cur_start, " ".join(cur_parts)))
    return paras


def render_video(info: dict, paras: list, kind: str) -> str:
    """One video's section of full_text.txt, with chapter markers inlined."""
    title = info.get("title", "Untitled")
    chapters = info.get("chapters") or []
    lines = [
        "=" * 78,
        f"=== VIDEO: {title}",
        f"=== Channel: {info.get('uploader') or info.get('channel', '?')}"
        f" | Duration: {fmt_ts(info.get('duration') or 0)}"
        f" | Captions: {kind}",
        f"=== URL: {info.get('webpage_url', '')}",
        "=" * 78,
        "",
    ]
    if chapters:
        lines.append("--- YouTube chapter map ---")
        for ch in chapters:
            lines.append(f"  [{fmt_ts(ch['start_time'])}] {ch.get('title', '')}")
        lines.append("")

    ch_iter = iter(chapters)
    next_ch = next(ch_iter, None)
    for start, text in paras:
        while next_ch and start >= next_ch["start_time"]:
            lines.append("")
            lines.append(f"## CHAPTER: {next_ch.get('title', '')} [{fmt_ts(next_ch['start_time'])}]")
            lines.append("")
            next_ch = next(ch_iter, None)
        lines.append(f"[{fmt_ts(start)}] {text}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", nargs="*", help="YouTube video or playlist URLs")
    ap.add_argument("--lang", default="en", help="caption language code (default: en)")
    ap.add_argument("--workdir", default=None, help="output work directory")
    ap.add_argument("--max-videos", type=int, default=25, help="playlist cap (default: 25)")
    ap.add_argument("--search", default=None, metavar="QUERY",
                    help="search YouTube and print results as JSON (no transcript fetch)")
    ap.add_argument("--max-results", type=int, default=8, help="search result count (default: 8)")
    ap.add_argument("--check", action="store_true", help="verify environment and exit")
    args = ap.parse_args()

    if args.check:
        check_env()
        return
    if not shutil.which("yt-dlp"):
        die("yt-dlp not found on PATH. Install: brew install yt-dlp")
    if args.search:
        print(json.dumps(search_videos(args.search, args.max_results),
                         indent=2, ensure_ascii=False))
        return
    if not args.urls:
        ap.error("at least one URL is required (or use --search / --check)")

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / "yt_skill_work"
    workdir.mkdir(parents=True, exist_ok=True)

    video_urls = []
    for u in args.urls:
        video_urls.extend(list_videos(u, args.max_videos))
    print(f"Videos to process: {len(video_urls)}", file=sys.stderr)

    sections, sources = [], []
    for i, vurl in enumerate(video_urls, 1):
        info = get_info(vurl)
        vid, title = info.get("id", f"v{i}"), info.get("title", "Untitled")
        print(f"[{i}/{len(video_urls)}] {title}", file=sys.stderr)

        cap_path, kind = download_captions(vurl, vid, args.lang, workdir)
        if not cap_path:
            print(f"  WARNING: no '{args.lang}' captions (manual or auto) — skipping transcript",
                  file=sys.stderr)
            sources.append({"id": vid, "title": title, "url": info.get("webpage_url", vurl),
                            "channel": info.get("uploader"), "duration_s": info.get("duration"),
                            "captions": None, "words": 0,
                            "chapters": [c.get("title") for c in info.get("chapters") or []]})
            continue

        paras = events_to_paragraphs(parse_json3(cap_path))
        section = render_video(info, paras, kind)
        sections.append(section)
        words = sum(len(t.split()) for _, t in paras)
        sources.append({"id": vid, "title": title, "url": info.get("webpage_url", vurl),
                        "channel": info.get("uploader"), "duration_s": info.get("duration"),
                        "captions": kind, "words": words,
                        "chapters": [c.get("title") for c in info.get("chapters") or []]})

    if not sections:
        die("no transcripts could be fetched for any input URL")

    full_text = workdir / "full_text.txt"
    full_text.write_text("\n".join(sections), encoding="utf-8")

    total_words = sum(s["words"] for s in sources)
    meta = {
        "total_sources": len(sources),
        "total_words": total_words,
        "estimated_tokens": int(total_words * 1.35),
        "total_duration_s": sum(s.get("duration_s") or 0 for s in sources),
        "lang": args.lang,
        "sources": sources,
        "full_text_path": str(full_text),
    }
    (workdir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                           encoding="utf-8")

    print(json.dumps({"workdir": str(workdir), "videos": len(sources),
                      "words": total_words, "estimated_tokens": meta["estimated_tokens"]}))


if __name__ == "__main__":
    main()
