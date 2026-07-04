---
name: youtube-to-skill
description: "Watches YouTube videos and playlists (talks, lectures, courses, tutorials) by fetching transcripts via yt-dlp — answering questions about a video in-chat (Quick Watch), searching YouTube for videos on a topic, or converting videos into structured agent skills with frameworks, mental models, techniques, and anti-patterns. Use when the user shares a YouTube URL, asks what a video says or teaches, says 'watch/find videos about X', or wants a video/playlist turned into a reusable knowledge base or study skill."
---

<!-- argument-hint: <youtube-url>... [skill-name-slug] | "find videos about <topic>" -->
<!-- Script paths below are relative to this skill's directory. -->

# YouTube-to-Skill Converter

Transform video knowledge into actionable agent skills by extracting structure from transcripts — not producing summaries. Output format follows the conventions of [book-to-skill](https://github.com/virgiliojr94/book-to-skill) (MIT, virgiliojr94), so book-derived and video-derived skills read the same way; this skill is fully self-contained and does not require it.

## Philosophy

Conference talks, lecture series, and course playlists contain crystallized expertise. This skill pulls the transcript (manual subtitles when available, auto-captions otherwise) plus YouTube chapter markers, and extracts:
- Named frameworks (exact formulations, speaker's own naming)
- Actionable principles ("Use X when Y")
- Techniques (step-by-step methods)
- Anti-patterns (what to avoid and why)

**Honesty rule — video is lossy.** Three failure modes a book skill doesn't have; always disclose them to the user:
1. **Auto-captions mangle technical terms** ("Raft" → "raft/rapht"). Fix obvious ones from context; flag uncertain ones with `(sp?)`.
2. **Slides, diagrams, and on-screen code never enter the transcript.** The optional **visual pass** (Step 2.7) recovers them by extracting scene-change keyframes the agent reads as images. Without it, note gaps as `[visual content not captured]` rather than inventing content.
3. **Speech is redundant and unstructured** — a 60-minute talk is often ~9k words with maybe 3 real frameworks. Never pad thin material to look like a book.

---

## Modes of Operation

Route by intent — **default to the lightest mode that answers the user**:

0. **Quick Watch** — user shares a URL with a question or casual intent ("what does this video say about X?", "summarize this talk") and has NOT asked for a skill. Run Step 2 only (fetch transcript), probe it (Step 2.6 style), and answer **in the conversation** with `(H:MM:SS)` timestamps. No files, no content-type question, no cost ceremony. If the answer hinges on visuals, offer the frame extractor for the relevant range. End by offering: "Want this as a permanent skill? The transcript is already fetched." — if yes, continue at Step 2.5 with the existing workdir.

0.5. **Search / Discover** — user has a topic but no URL ("find/watch videos about X"):
   ```bash
   python3 scripts/fetch_transcript.py --search "X" --max-results 8
   ```
   Prints JSON results (title, channel, duration, views, url). "find/search videos" → show the table, let the user pick. "watch videos about X" → pick the top 2–3 yourself, favoring longer talks from recognizable channels over shorts, say what you picked, then proceed as Quick Watch (or Full Conversion if they asked for a skill).

Skill-building modes:

1. **Full Conversion** — URL(s) given with skill-building intent: run Steps 0–10.
2. **Analyze Only** — user says "analyze" / "just extract": run Steps 0–3, output the extraction report, stop.
3. **Generate from Prior Analysis** — skip Steps 0–3, run 4–10 from provided notes.
4. **Update / Fold-in** — new video URLs for an existing skill (slug exists in `SKILLS_HOME` or user says update): fetch transcripts (Steps 1–2), then run the **Update / Fold-in Workflow** at the end of this file.

---

## Step 0 — Out-of-scope check

If no URL argument: if the user gave a **topic**, switch to Search/Discover (Mode 0.5). Otherwise stop with
> "youtube-to-skill needs a YouTube URL or a topic to search. Usage: `youtube-to-skill <url>... [skill-name-slug]` or 'find videos about <topic>'"

Parse arguments:
- Anything matching a YouTube URL (`youtube.com/watch`, `youtu.be/`, `youtube.com/playlist`) → `INPUT_URLS`.
- A trailing lowercase-hyphen token that is not a URL → `SKILL_NAME`.
- If `SKILL_NAME` matches an existing skill in `SKILLS_HOME` → Mode 4.

## Step 1 — Validate environment

```bash
python3 scripts/fetch_transcript.py --check
```
If yt-dlp is missing, give the user the install command and stop.

## Step 1.5 — Identify content type

Ask (skip if obvious from the URL/title):
> "What kind of video is this?
> 1. **Technical** — talk/lecture with code, architectures, precise terminology
> 2. **Conceptual** — mostly ideas, frameworks, career/soft-skill content"

Store as `VIDEO_TYPE` (`technical` / `conceptual`) — it drives the Step 7 budget matrix. Also ask for the caption language if the video might not be English (default `en`).

## Step 2 — Fetch transcript(s)

```bash
python3 scripts/fetch_transcript.py <INPUT_URLS> --lang <LANG> --workdir "${TMPDIR:-/tmp}/yt_skill_work"
```

Produces in the workdir:
- `full_text.txt` — all transcripts, per-video headers, `## CHAPTER:` markers from YouTube chapter data, `[H:MM:SS]` paragraph timestamps.
- `metadata.json` — per-video title/channel/duration/chapters/caption-kind (manual vs auto)/word counts + totals.

Read `metadata.json`. **If any video used `"captions": "auto"`, tell the user** transcript quality is auto-caption grade and technical terms will need correction. If a video has `"captions": null`, report it as skipped and continue with the rest.

## Step 2.5 — Pre-flight cost estimate

Present before generating; wait for confirmation:
```
Videos: <N>  (<list: title — duration — manual/auto captions>)
Words: ~<N> | Tokens: ~<N>K | Total runtime: <H:MM>
Estimated: input ~<N>K + output ~<N>K tokens
(+ visual pass, if chosen in Step 2.7: ~1.2K input tokens per frame, ~<N> frames expected)
Proceed? (or "analyze only" to preview first)
```
Input ≈ `estimated_tokens` × 1.3; output ≈ chapters × per-chapter budget (Step 7 matrix) + 4k (SKILL.md) + 4.5k (supporting files).

## Step 2.6 — Probe, don't slurp (transcripts > 50k tokens)

For long playlists/courses, don't `Read` all of `full_text.txt`. Probe it:
```bash
grep -n "^=== VIDEO:\|^## CHAPTER:" "$WORKDIR/full_text.txt"   # structure map
sed -n '<start>,<end>p' "$WORKDIR/full_text.txt"               # one section
grep -c -i "<framework name>" "$WORKDIR/full_text.txt"         # verify before claiming
```

## Step 2.7 — Optional visual pass (slides / on-screen code / diagrams)

Offer whenever `VIDEO_TYPE=technical`, the video is slide-driven, or the user asks for visuals:

> "Want me to also capture the visuals? I'll download the video, extract a keyframe at each slide/scene change, and read the frames to transcribe code, tables, and diagrams into the chapters. Adds ~1.2K tokens per frame (typically 30–60 frames per talk)."

If yes, per video:
```bash
python3 scripts/extract_frames.py "<url>" --workdir "${TMPDIR:-/tmp}/yt_skill_work" --max-frames 60
```
Requires ffmpeg (`--check` prints install hints). Produces `frames_<id>/f*.jpg` + `frames.json` (`[{file, t_seconds, ts}]`).

Tuning:
- Camera-cut-heavy videos (speaker ↔ slides) fire many duplicate scene changes — raise `--scene` to 0.4 if most frames are the speaker's face.
- Dense screencasts (live coding) may need `--scene 0.2` and a higher `--max-frames`.
- Frames are 720p by default; use `--height 1080` if on-screen code is small.

**Using the frames (during Step 7):** for each chapter, look up in `frames.json` the frames whose `t_seconds` fall inside that chapter's time range, `Read` them (they are images), and:
- Skip frames that show only the speaker/venue — spend no output on them.
- Transcribe on-screen **code** exactly as shown into Code Examples, cited `(slide @ 12:34)`.
- Reproduce **tables/lists** from slides in Reference Tables.
- **Diagrams**: describe structure compactly or render as ASCII/mermaid if simple.
- Where a frame contradicts the caption text, the slide wins — fix the term.

## Step 3 — Analyze structure

Chapter mapping, in priority order:
1. **Playlist** → each video = one chapter file.
2. **Single video with YouTube chapters** (`## CHAPTER:` markers present) → each marker = one section; merge sub-minute chapters into neighbors.
3. **Single video, no chapters** → segment by topic shifts in the transcript ("so now let's talk about…"); aim for 3–8 sections for a talk, more for a multi-hour course.

Identify: speaker(s), talk/course title, event/channel, core themes.

**In Analyze Only mode**, output this report and stop:
```
## Extraction Report — <Title>
### Speaker's Core Frameworks
- **<Name>**: <what it is and when to apply> (H:MM:SS)
### Key Principles / Techniques / Anti-patterns
### Suggested Skill Name: yt-<speaker>-<concept>
### Sections Detected
| # | Title | Timestamp | Main Frameworks |
```

## Step 4 — Ask purpose

> "What should this skill help you do?
> 1. Apply the speaker's frameworks while working
> 2. Think with the speaker's mental models
> 3. Reference specific sections and concepts
> 4. All of the above"

Derive `DEPTH`: only option 3 → `DEPTH=reference` (lean, fast-lookup chapters); otherwise `DEPTH=study` (worked detail, examples, reasoning). In Modes 3/4 default `DEPTH=study`.

## Step 5 — Determine skill name and destination

Default slug: `yt-{speaker-lastname}-{core-concept}` (e.g. `yt-kleppmann-event-streams`); for channel courses `yt-{channel-or-course}-{topic}`. The `yt-` prefix groups video-derived skills in listings — drop it only if the user asks. If `SKILL_NAME` was provided, use it.

`SKILLS_HOME` = the host agent's personal skill root (Claude Code: `~/.claude/skills`; project-local `.claude/skills` if the user asks). **Do NOT nest generated skills in a subfolder** (e.g. `~/.claude/skills/youtube-skills/<name>/`) — Claude Code only discovers skills one level deep, so nested skills silently never load.

If `$SKILLS_HOME/<skill_name>/` exists, ask: Update/Fold-in (Mode 4), Overwrite, or Rename.

## Step 6 — Create directory

```bash
mkdir -p "$SKILLS_HOME/<skill_name>/chapters"
```

## Step 7 — Generate chapter files

Per-chapter token budget (target, not hard cap — density beats length, never pad):

| | `DEPTH=reference` | `DEPTH=study` |
|---|---|---|
| `conceptual` | 800–1,200 | 1,000–1,800 |
| `technical` | 1,200–1,800 | 2,000–3,000 |

Study depth is earned with content: a reconstructed worked example, expanded "how" steps per framework, a failure-mode note on the top frameworks. If a section is thin, let it land short and say so in its Core Idea.

For EACH chapter/section from Step 3, pull only its transcript slice (Step 2.6 probes) and matching frames (Step 2.7), then create `chapters/ch<NN>-<slug>.md`:

```markdown
# Chapter N: <Title>

## Core Idea
<1–2 sentences>
**Watch**: <url>&t=<start-seconds>s

## Frameworks Introduced
- **<Name>** (H:MM:SS): <exact formulation — the speaker's own naming>
  - When to use: <situation>
  - How: <steps or criteria>

## Key Concepts
- **<Term>** (H:MM:SS): <one-sentence definition>

## Anti-patterns
- **<What to avoid>**: <why it fails> (H:MM:SS)

## Code Examples  *(technical + visual pass only)*
```<language>
<code transcribed from frame>
```
- Source: (slide @ H:MM:SS)

## Reference Tables  *(technical + visual pass only)*
<tables reproduced from slides>

## Worked Example  *(DEPTH=study only)*
<the demo/walkthrough the speaker performs, reconstructed compactly;
 where it depended on visuals not captured, say so explicitly>

## Key Takeaways
1–5 actionable insights, each with its timestamp

## Connects To
- **Ch N**: <relation> | **<external concept>**: <relation>
```

Every extracted claim carries its `(H:MM:SS)` — the video equivalent of a page number. Q&A segments: extract only questions that produced real content.

## Step 8 — Generate supporting files

- **glossary.md** — significant terms, alphabetical, `**Term** — definition (ch N, H:MM:SS)`. Max ~1,500 tokens.
- **patterns.md** — concrete techniques: `## Pattern\n**When to use** / **How** / **Trade-offs**`. Max ~2,000 tokens.
- **cheatsheet.md** — the speaker's *judgment*, not a keyword list: decision rules ("When X, do Y, because Z"), decision trees, trade-off tables, thresholds and defaults, tells and smells. Every line should help the reader decide something. Max ~1,200 tokens.

## Step 9 — Generate the master SKILL.md

Keep the body under ~4,000 tokens; most important content first (compaction truncates from the end).

```markdown
---
name: <skill_name>
description: "Knowledge base from \"<Talk/Course Title>\" by <Speaker> (<Channel/Event>). Use when applying <speaker>'s frameworks for <3–6 key topics>, studying the material, or referencing its concepts."
---

# <Title>
**Speaker**: <name> | **Channel/Event**: <name> | **Runtime**: <H:MM> | **Videos**: <N> | **Source**: <url> | **Generated**: <YYYY-MM-DD>

## How to Use This Skill
- Without arguments — load core frameworks
- With a topic — I find and read the relevant chapter
- With a chapter number — I load that chapter
- Ask "what chapters do you have?" for the index

## Core Frameworks & Mental Models
<~2,000 tokens: the speaker's most important named frameworks.
 "Use X when Y", "Prefer X over Y because Z". A toolkit, not a summary.>

## Chapter Index
| # | Title | Timestamp | Key Frameworks |
|---|-------|-----------|----------------|
| [ch01](chapters/ch01-<slug>.md) | <Title> | 0:00 | <frameworks> |

## Topic Index
- **<Term>** → ch<N>[, ch<N>]

## Supporting Files
- [glossary.md](glossary.md) | [patterns.md](patterns.md) | [cheatsheet.md](cheatsheet.md)

## Scope & Limits
Built from <manual|auto> captions<, + visual pass (<N> frames transcribed) | ; visual content (slides, live code) not captured>. Covers this material only.
```

## Step 10 — Cleanup and report

```bash
rm -rf "${TMPDIR:-/tmp}/yt_skill_work"
```

Report: skill path, source video(s) with runtime, files generated with approximate token sizes, usage examples ("ask <skill_name> about <topic>"), and a reminder that the session must be restarted for the new skill to load.

---

## Update / Fold-in Workflow (Mode 4)

1. Read the existing skill's `SKILL.md` (chapter index, topic index, metadata), list `chapters/` to find the highest chapter number, and read the three supporting files.
2. Decide per new video/section: **revision** of an existing chapter (merge into that file) or **addition** (new `ch<N+1>-*.md`, numbered after the highest existing).
3. Generate or update chapter files per Step 7.
4. Merge supporting files: glossary (alphabetize; existing terms get appended references), patterns (append new, keep under ~2,500 tokens), cheatsheet (integrate new decision rules).
5. Regenerate master SKILL.md: bump video count/runtime, add sources, fold new frameworks into Core (stay under 4k tokens), append to both indexes, update Generated date.
6. Cleanup and report as in Step 10, noting what was added vs merged.

---

## Quality Rules

1. **Extract structure, not summaries** — named frameworks, exact formulations, anti-patterns; not recaps.
2. **Preserve the speaker's precision** — keep their exact naming.
3. **Density over completeness** — a 1,000-token chapter beats a 10,000-token excerpt.
4. **Practitioner voice** — "Use X when Y", not "The speaker explains X".
5. **Front-load SKILL.md** — most important content first.
6. **Chapter files are on-demand** — they cost nothing until loaded.
7. **Never copy raw transcript** — synthesize; extract signal.
8. **Topic index is critical** — it's how the agent navigates to the right chapter.
9. **Timestamps are citations** — every extracted framework carries the `(H:MM:SS)` where the speaker states it.
10. **Correct captions, don't trust them** — silently fix caption errors that context makes certain; mark genuinely ambiguous names `(sp?)` instead of guessing.
11. **Frames are evidence, not filler** — transcribe only frames that add content; a speaker-face frame costs a Read but must cost zero output tokens. Slide text beats auto-caption audio when they disagree.
