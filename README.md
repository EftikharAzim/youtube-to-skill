# youtube-to-skill

A [Claude Code](https://claude.com/claude-code) skill that turns YouTube videos and playlists into structured, reusable agent skills — or just answers questions about a video without creating any files.

It fetches transcripts with yt-dlp (manual subtitles preferred, auto-captions as fallback), maps YouTube chapter markers to sections, and optionally extracts slide keyframes with ffmpeg so the agent can read on-screen code, tables, and diagrams that never appear in a transcript.

## What it produces

A skill in the same shape as [book-to-skill](https://github.com/virgiliojr94/book-to-skill) output, so book-derived and video-derived knowledge bases read identically:

```
yt-speaker-topic/
├── SKILL.md          core frameworks, chapter index, topic index
├── chapters/         one file per video (playlists) or per chapter marker
├── glossary.md       terms with definitions, cited (ch N, H:MM:SS)
├── patterns.md       techniques with when-to-use / how / trade-offs
└── cheatsheet.md     decision rules, thresholds, trade-off tables
```

Every extracted claim carries the timestamp where the speaker states it, and each chapter links to `<url>&t=<seconds>s` so you can jump to the source moment.

## Modes

| You say | What happens |
|---|---|
| "What does this talk say about leader election? \<url\>" | Answers in-chat with timestamps. No files. |
| "Find videos about Raft consensus" | Searches YouTube, shows title/channel/duration/views, you pick. |
| "Watch videos about X" | Picks the top 2–3 talks itself and answers from their transcripts. |
| `/youtube-to-skill <url>` | Full conversion: content-type question → cost estimate → generates the skill. |
| `/youtube-to-skill <playlist-url> my-slug` | Same, each playlist video becomes one chapter. |
| "Add this video to yt-my-course: \<url\>" | Appends new chapters to an existing generated skill. |

Conversion always shows a token-cost estimate and waits for confirmation before generating anything.

## Requirements

- Claude Code
- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — transcript fetching and search
- [ffmpeg](https://ffmpeg.org/) — only for the optional visual pass

```sh
brew install yt-dlp ffmpeg      # macOS
```

## Install

```sh
git clone https://github.com/EftikharAzim/youtube-to-skill ~/.claude/skills/youtube-to-skill
```

Restart Claude Code. Verify the environment:

```sh
python3 ~/.claude/skills/youtube-to-skill/scripts/fetch_transcript.py --check
python3 ~/.claude/skills/youtube-to-skill/scripts/extract_frames.py --check
```

## How it works

1. **Transcript** — `scripts/fetch_transcript.py` resolves the URL (video, playlist, or `--search "query"`), downloads json3 captions via yt-dlp, and writes a single `full_text.txt` with per-video headers, `## CHAPTER:` markers taken from YouTube's own chapter data, and `[H:MM:SS]` paragraph timestamps, plus a `metadata.json` with word/token counts and whether captions were manual or auto.
2. **Visual pass (optional)** — `scripts/extract_frames.py` downloads the video stream (720p by default, video-only), runs ffmpeg scene-change detection to grab one keyframe per slide/screen transition, and writes `frames.json` mapping frames to timestamps. The agent reads the frames as images and transcribes code, tables, and diagrams into the chapters. When a slide and the auto-caption disagree on a term, the slide wins.
3. **Generation** — the agent analyzes structure (playlist videos → chapters; else YouTube chapter markers; else topic-shift segmentation), then writes the skill files under explicit per-chapter token budgets. Long transcripts are probed with grep/sed rather than loaded whole.

Both scripts work standalone if you just want transcripts or keyframes:

```sh
python3 scripts/fetch_transcript.py "https://youtube.com/watch?v=..." --lang en
python3 scripts/fetch_transcript.py --search "raft consensus" --max-results 8
python3 scripts/extract_frames.py "https://youtube.com/watch?v=..." --max-frames 60
```

Useful flags: `--lang` (caption language), `--max-videos` (playlist cap, default 25), `--scene` (scene-change threshold; raise to 0.4 for camera-cut-heavy talks, lower to 0.2 for screencasts), `--height` (frame resolution, default 720).

## Limitations

- **No captions, no transcript.** Videos without manual or auto captions are skipped. Whisper transcription is not included.
- **Auto-captions garble technical terms.** The workflow corrects errors that context makes certain and marks ambiguous ones `(sp?)`, but treat auto-caption-derived glossaries with some suspicion. `metadata.json` tells you which kind you got.
- **Visuals cost tokens.** The visual pass reads each frame as an image (~1.2K tokens per frame, typically 30–60 frames per talk). It is opt-in and priced in the pre-flight estimate.
- **Slide legibility depends on the source.** Full-screen slides and screencasts read cleanly at 720p; small picture-in-picture slide insets in older webcasts may not.
- **yt-dlp decays.** YouTube changes break it periodically; keep it updated.

## Credits

Output format and generation methodology adapted from [book-to-skill](https://github.com/virgiliojr94/book-to-skill) by virgiliojr94 (MIT). This skill is self-contained and does not depend on it.

## License

MIT
