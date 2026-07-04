# youtube-to-skill

A [Claude Code](https://claude.com/claude-code) skill that turns YouTube videos and playlists into structured, reusable agent skills — or just answers questions about a video without creating any files.

It fetches transcripts with yt-dlp (manual subtitles preferred, auto-captions as fallback), maps YouTube chapter markers to sections, and optionally extracts slide keyframes with ffmpeg so the agent can read on-screen code, tables, and diagrams that never appear in a transcript.

## Why

A conference talk or lecture series you watched last month is gone from working memory. Any chat assistant can summarize a video on demand, but a summary is ephemeral prose — you re-read it top to bottom, and it dies in the chat history. A skill is a file your coding agent loads on demand while you work: ask about one concept and it pulls the one chapter that covers it, cites the timestamp where the speaker said it, and links you to that exact second of the video. A twelve-lecture course costs you one chapter per question, not the whole course.

Transcripts alone also lose whatever was on screen. The optional visual pass reads slide keyframes, so the code, tables, and diagrams a speaker showed — but never spoke aloud — end up in the chapters too.

Convert a playlist once; from then on your agent answers from it, with sources.

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

## Usage

| You say | What happens |
|---|---|
| "What does this talk say about leader election? \<url\>" | Answers in-chat with timestamps. No files. |
| "Find videos about Raft consensus" | Searches YouTube, shows title/channel/duration/views, you pick. |
| "Watch videos about X" | Picks the top 2–3 talks itself and answers from their transcripts. |
| `/youtube-to-skill <url>` | Full conversion: content-type question → cost estimate → generates the skill. |
| `/youtube-to-skill <playlist-url> my-slug` | Same, each playlist video becomes one chapter. |
| "Add this video to yt-my-course: \<url\>" | Appends new chapters to an existing generated skill. |

Conversion always shows a token-cost estimate and waits for confirmation before generating anything. After a conversion, restart Claude Code to load the new skill.

## Requirements

- Claude Code
- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — transcript fetching and search
- [ffmpeg](https://ffmpeg.org/) — only for the optional visual pass

The scripts are standard-library-only Python; there is nothing to `pip install` for the skill itself.

## Install

### macOS

```sh
brew install yt-dlp ffmpeg
git clone https://github.com/EftikharAzim/youtube-to-skill ~/.claude/skills/youtube-to-skill
```

### Linux

Install ffmpeg from your distribution; install yt-dlp via pipx (distro packages of yt-dlp go stale quickly, and a stale yt-dlp stops working against YouTube):

```sh
# Debian/Ubuntu
sudo apt install ffmpeg pipx && pipx install yt-dlp
# Fedora (ffmpeg via RPM Fusion)
sudo dnf install ffmpeg pipx && pipx install yt-dlp
# Arch
sudo pacman -S ffmpeg yt-dlp

git clone https://github.com/EftikharAzim/youtube-to-skill ~/.claude/skills/youtube-to-skill
```

### Windows

Claude Code on Windows runs shell commands through Git Bash, which this skill's commands are written for. Install the tools with winget (or scoop/choco), then clone into your profile:

```powershell
winget install yt-dlp.yt-dlp Gyan.FFmpeg
git clone https://github.com/EftikharAzim/youtube-to-skill "$env:USERPROFILE\.claude\skills\youtube-to-skill"
```

Make sure `python3`, `yt-dlp`, and `ffmpeg` resolve inside Git Bash (`where yt-dlp` in a new terminal after install).

### Verify (all platforms)

Restart Claude Code, then:

```sh
python3 ~/.claude/skills/youtube-to-skill/scripts/fetch_transcript.py --check
python3 ~/.claude/skills/youtube-to-skill/scripts/extract_frames.py --check
```

Developed on macOS. CI runs the environment checks and parser tests on Linux, macOS, and Windows; full conversions have only been exercised on macOS. Reports welcome.

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
- **Scale.** Single talks and courses up to ~25 videos convert in one session (the playlist cap defaults to 25; raise with `--max-videos`). Longer playlists work in batches via the update mode, but fetching captions for very many videos in one run may hit YouTube rate limiting, and generation cost grows linearly with runtime — roughly 12–15K tokens of transcript per hour of speech before generation overhead.
- **Format matters.** Structured material (lectures, conference talks, courses) extracts well. Conversational content — podcasts, interviews, vlogs — yields thin skills, because there is little named structure to extract. Private, members-only, and age-restricted videos are not supported.

## Responsible use

This tool fetches captions and (optionally) video streams from YouTube, which YouTube's Terms of Service restrict; use it for personal study and research, and be aware that heavy use may get your IP rate-limited. Generated skills are derivative works of the source video — keep them private unless the video's license (e.g. Creative Commons) permits redistribution, and credit the speaker either way. You are responsible for how you use this tool.

## Credits

Output format and generation methodology adapted from [book-to-skill](https://github.com/virgiliojr94/book-to-skill) by virgiliojr94 (MIT). This skill is self-contained and does not depend on it.

## License

MIT
