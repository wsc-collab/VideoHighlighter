# Club montages

This fork's club path is a **talking pack** for Woodinville Sports Club and
Tier 1 (tennis, golf, APL): people speaking on camera. A folder of real
clips plus a `brief.md` becomes one file:

1. **Title** over about the first 2–3 seconds.
2. **Captions** of what is said through the middle, from local Whisper.
3. **Call to action** over the close.

Cuts are straight joins. The path does not build CapCut-style flashy
montages, and it does not generate B-roll, faces, voices, or songs. Silent
hype reels stay in CapCut Web: drop the clips there and edit the template
text. This repo does not automate CapCut.

If a moment is missing, the fix is another real clip or a different in/out
taken from `timeline.json` and written into the brief. `cuts.json` is the
plan the assembler used. Caption lines are the transcript. They are not
copy written in the brief.

The cut is chosen from the full transcript and the per-second motion, not
from a numbered group on a pick list. `timeline.json` puts both on one
clock. A quote list is still how a person names a line that has to be in.

The desktop highlighter is unchanged. Automated runs use the CLI below.

## Two-step talking pick

Ask two things before anything is encoded:

1. **LENGTH** — `30s`, `20-35s`, or **none**. None means the cut is only
   the moments named below. Do not invent a target runtime.
2. **Must-include moments or quotes** — lines the person wants kept, in
   their words. A quote that is not in the transcript is listed in
   `cuts.json` and is not invented.

The transcript can be made first, so those quotes are chosen from what
was actually said. Encoding waits until both answers are in `brief.md`.

1. **Transcript and motion.** Whisper reads every clip and writes
   `transcript.md` (readable, with times) and `transcript.json` (per file,
   segment times, and word times). The same pass measures motion and
   writes `timeline.json` (and `timeline.md`). No `draft.mp4`.

   ```bash
   python -m modules.club pick "/path/to/clips"
   python -m modules.club "/path/to/clips" --transcript-only
   ```

   The command prints `transcript.md: ...` and `timeline.json: ...`.
   Show the transcript so a person can name must-include quotes. Choose
   in and out from the timeline. Do not pick a window index or a group
   number off `scores.json`. That grid is only a fallback fill when
   LENGTH is set and no ranges were named.

2. **Assemble.** Add the choice to `brief.md`, then run without `pick`.
   The title, captions, and optional call to action are unchanged.

   A quoted line or a short moment:

   ```text
   MUST_INCLUDE: "hold your finish"
   ```

   Several moments, one per line. A file name before the colon searches
   only that clip:

   ```text
   MUST_INCLUDE:
   - lesson.mp4: "hold your finish"
   - we play at four
   ```

   Or a file and a range, in seconds or `m:ss`:

   ```text
   INCLUDE_WINDOWS:
   lesson.mp4 0:12-0:18
   rally.mov 12.0-18.5
   ```

   Those windows are cut in. When LENGTH is a duration, the ranker still
   fills whatever is left of it. On a talking cut that fill is high speech,
   then action. `LENGTH: none` does not fill: the named moments are the cut.
   With none and no moments, nothing is encoded.
   A silent or low-motion window ranks at the bottom. It is not placed
   in the middle of the pack, and it is not used at all while a talking
   or action window is still available. When the transcript cues a beat —
   pause, hold, check feet, or the same kind of setup ("watch this",
   "ready", "here we go", "finish") — that window stays up two more
   seconds so the cut does not end on the last word, when those two
   seconds still fit in `LENGTH`. They do not push the draft past the
   brief. Other filler is tightened first, and the hold is shortened
   only when that is the only way to stay on `LENGTH`. This is the
   talking default for every clip. `KEYWORDS` do not turn it on. Leave both
   fields out, with a LENGTH set, and the ranker chooses on its own,
   as before. `LENGTH: none` with both fields empty writes no cut.
   A quote that is not in the transcript is listed in
   `cuts.json` under `includes` and is not invented. An
   `INCLUDE_WINDOWS` file that is not in the folder stops the run.
   A window that was kept and then does not appear in the final cut
   list stops the run as well. The assemble step does not drop a
   highlight to make the length.

   The assemble step reads `transcript.json` when it is already there, so
   the folder is not transcribed again. `pick` hears every file, including
   a quiet phone take. A same-session lesson mic is ranked with the rest.
   A file far under the loudest (boom mixed with a phone) stays in the
   pool with a small score haircut. A highlight that names it is kept.

## brief.md

Put `brief.md` in the clips folder. Length and style are required. The
type fields are optional: leave them out and the run stays a highlights cut.

```text
LENGTH: 20s
STYLE: talking
BRAND: tier1
TITLE: Private lesson with Coach John Wang
TITLE_ACCENT: John Wang
FONT: Inter
KEYWORDS: lesson, finish
NOTES: Prefer windows where the coach is speaking. Do not use a spoken line as the title.
```

`TITLE` is who is on camera and what the video is. It is not a coaching
cue from the transcript (`Hold your finish` belongs in the captions, not
in the opening title). `KEYWORDS` and `NOTES` still steer which windows
are kept. They are not drawn on screen.

Leave `CTA` out when there is no end card. A soft line is optional:

```text
CTA: Book a lesson
```

`BRAND` picks the face when `FONT` is omitted. `wsc` and `bsc` use
**Inter**. `tier1` (golf and tennis Tier 1) also uses **Inter** until a
Tier 1 display face is named. Do not set Interwald, and do not switch
Tier 1 to Oswald. An explicit `FONT`
overrides the brand. John Wang packs are Tier 1, so the face is Inter.
The title is bold white Inter, horizontally centered, on two or three
lines at a large size, with a soft shadow and no stroke and no plate.
`TITLE_ACCENT` is the span that switches to a script face (the coach's
name), on its own line. Pacifico is preferred; Dancing Script is the
fallback. The rest stays Inter Bold.
A call to action, when the brief sets one, uses the title's look at a
slightly smaller size. Leave `CTA` out and the close is not a blank card:
captions run through the end. Captions stay one line: white with a black
stroke, sized so a short cue of a few words covers about three quarters
of the frame width, and still smaller than the title. A phrase that
would overflow becomes the next timed caption, not a second line on
screen. `TITLE` is still only the brief line.

Install Inter where the app looks for it:

- Mac: `~/Library/Fonts/Inter-Regular.otf` (`.ttf` is fine)
- This clone: `fonts/Inter-Regular.otf` (on the club Mac,
  `~/Desktop/Marketing/Grok Bot Work/VideoHighlighter/fonts/Inter-Regular.otf`)
- Windows: `C:\Windows\Fonts\Inter-Regular.otf`

If Inter is missing, the run logs that and uses a bold sans already on
the machine (DejaVu Sans Bold or Arial). A WSC or BSC brief:

```text
BRAND: wsc
FONT: Inter
```

Leave `COLORS` out. On a talking pack nothing sits behind the words.
Title and CTA are bold white with no stroke. Captions are white with a
black stroke. A `COLORS` line does not paint a plate and does not recolor the
words, so an older brief that still says `#1B4D3E, #F4E8C1` will not come
back as a green card or a dark bar with orange type. `COLORS` still
paints a `hype` highlights card.

Leave `CAPTION_SIZE` and `CAPTION_POSITION` out. The defaults put the
center of each caption at two-fifths of the frame height up from the
bottom (`two_fifths`, Y ≈ 0.6 × height), horizontally centered, one line
at a time, about four words. That is below the middle of the frame. The
point size scales with the frame so a short cue covers about
75% of the width, and stays under the title. A line that would be wider
than the frame is the next caption cue. A brief copied from an earlier
draft that still says `CAPTION_POSITION: bottom`, `CAPTION_POSITION: center`,
or `CAPTION_SIZE: 42` keeps that old look. Delete those lines to use the
default. Set them only to override:

```text
CAPTION_SIZE: 72
CAPTION_POSITION: bottom
```

| Field | Required | Meaning |
| --- | --- | --- |
| `LENGTH` | yes | Finished draft length. `20-35s` is a range. `30s` is exact. `none` keeps only the named moments and does not fill a runtime. |
| `STYLE` | yes | `talking` for this pack. `hype` only ranks louder, busier windows and does not burn speech captions. |
| `KEYWORDS` | no | Words to boost when local Whisper hears them while choosing windows. Not caption text. |
| `MUST_INCLUDE` | no | Quoted lines or short moments to force into the cut. Mapped to the transcript. One per line. |
| `INCLUDE_WINDOWS` | no | `file start-end` ranges to force in. Seconds or `m:ss`. |
| `NOTES` | no | Copied into `cuts.json` for the editor. Not a prompt and not a caption. |
| `TITLE` | no | Who and what the video is, about the first 2–3 seconds. Inter Bold, white, centered, two or three large lines, soft shadow, no stroke, no plate. Not a line from the transcript. |
| `TITLE_UNDER` | no | Who is on camera under the title. Alias `OPEN_SCENE`. Not drawn. A talking window whose file name or transcript matches it leads. A slash separates alternatives (`John / pink shirt`). A phrase with no slash still has to occur as written. Example: `John`. |
| `TITLE_ACCENT` | no | The span inside `TITLE` drawn in a script face, such as `John Wang`, on its own line. The rest stays Inter Bold. |
| `SUBTITLE` | no | Optional second line with the title. Same bold white, no stroke. |
| `COLORS` | no | Ignored for talking type. On `hype`, the first hex is the bar and the second is the type. |
| `BRAND` | no | `tier1` (golf and tennis Tier 1), `wsc`, or `bsc`. Picks the default face when `FONT` is omitted. |
| `FONT` | no | A `.ttf` / `.otf` path, or a font name. Default is Inter for `tier1`, `wsc`, and `bsc`. |
| `CTA` | no | Optional close, about the last 2–3 seconds. Same as the title (bold white, centered, no stroke, no plate) at a slightly smaller size. Leave it out for no end line. |
| `CAPTION_COLOR` | no | Drawn as white or black. Default is white. A cream or orange value is drawn as white. |
| `CAPTION_STROKE` | no | Outline. Default is black behind white type, or white behind black type. Width example: `#000000 3`. |
| `CAPTION_SIZE` | no | Caption point size. Default scales with frame width so a short cue covers about 75% of the width, and stays smaller than the title. |
| `CAPTION_POSITION` | no | `two_fifths` (talking default: vertical center of the line at two-fifths of the frame height up from the bottom, Y ≈ 0.6 × height, below the middle, still horizontally centered), `center`, `bottom`, or `top`. `middle` is the same as `center`. `0.4`, `two-fifths`, and `2/5` are the same as `two_fifths`. A non-talking brief that omits the field stays at `center`. |
| `CAPTION_FONT` | no | Caption face. Falls back to `FONT`. |

`talking` favours speech and keywords, and plays clips in name order, then
time order, so a conversation stays in sequence. The first cut is the
exception: `TITLE_UNDER` or `OPEN_SCENE` when that match is someone
talking, otherwise a must-include, otherwise a window with speech. A
silent still does not play under the title while a talking window is in
the cut. After the opener, silent stills go to the end of the pack.
The ranker fills the rest of `LENGTH` with high-speech windows, then
action. It does not spend that time on a silent or low-motion window
while talking or action is still available. A Whisper line that is digit
spam or has no words is not speech. A clip whose mean volume is room
tone cannot fill a gap either, even if that transcript covers the file
and the picture moves. The extra time comes from the real speech after
a must-include instead. A setup line keeps the picture up for two
seconds after the words: pause, hold, check feet, and the same kind of
foreshadow (watch, ready, finish, here we go). Every talking clip gets
that hold. `KEYWORDS` only affect which windows rank higher.
`hype` favours audio peaks
and motion. Use it only when you want a highlights cut inside this tool.
A silent hype montage belongs in CapCut, not here.

Heading form works too (`## LENGTH` on its own line, value underneath).
A colon inside `NOTES` stays part of the note.

Keywords are supplied per brief. The app has no built-in sport category list.

## Talking pack

After the clips are joined, `STYLE: talking` runs one more local pass on
`draft.mp4`:

- **Title** from `TITLE` (and `SUBTITLE`, if you set one) for about 2.5
  seconds. The words are the brief, not a sentence Whisper heard. The
  type is Inter Bold, white, and horizontally centered, in two or three
  large lines, with a soft shadow and no stroke and no plate.
  `TITLE_ACCENT: John Wang` draws that span on its own line in Pacifico,
  or Dancing Script when Pacifico is not installed. Both are heavier
  and more readable on a phone than a thin calligraphy face. On a cut
  under about eight seconds the opening shrinks so it cannot cover the
  captions. Captions stay off during the title. Speech that is still
  going when the title clears is captioned from that instant, including
  a line that began under the title. A line that finished before the
  title ends stays off.
  Install
  `Inter-Bold.otf` next to Inter when you want the real bold face;
  otherwise the regular face is used.
- **Captions** for speech between the title and the call to action.
  A line still being said when the title clears starts then, not after
  a delay for the next word. Default position is the center of the line
  at two-fifths of the frame height up from the bottom (Y ≈ 0.6 × height,
  below the middle), horizontally centered, about four
  words, one line on screen. The size scales with the frame so a short
  cue covers about 75% of the width, and stays smaller than the title.
  Type is white with a black stroke and no box. A phrase that would
  overflow is the next timed cue, not a second line. The engine is
  local Whisper, in English, model `medium` unless you pass another.
  `base` mis-heard short cues, and `small` still drops words a lesson
  needs. `faster-whisper` is used when that
  package is installed, otherwise `openai-whisper` from
  `requirements.txt`. No caption is sent to a paid API.
  The cues are written to `captions.md` before the burn, and
  `captions.ass` is the file ffmpeg burns. Edit `captions.md` and run
  again to burn the correction. Delete `captions.md` to transcribe
  again. `--no-whisper` leaves the captions off and does not invent
  lines to fill them, unless `captions.md` is already there.
- **Call to action** from `CTA`, only when that line is set, for about
  the last 2.5 seconds. Same as the title — bold white, horizontally
  centered, soft shadow, no stroke, no plate — at a slightly smaller
  size. No `CTA` means no end card.

`cuts.json` lists every burned line under `captions.cues` with start, end,
and text, so you can check the words against the recording. A failed burn
keeps the unbranded `draft.mp4` and sets `captions.error`.

Whisper is already in `requirements.txt`. The first run downloads the
model weights into the local cache. Install ffmpeg as below; the caption
burn needs ffmpeg built with libass (`subtitles` filter), which a normal
ffmpeg package includes. `faster-whisper` is optional:

```bash
pip install faster-whisper
```

It is MIT-licensed, runs on CPU, and is used only if the import succeeds.
The app does not add a CapCut client or a web product surface for this pack.

## Run

From the repository root, with the virtualenv active:

```bash
python -m modules.club "/path/to/clips"
```

The same entry from the desktop script, still without opening the GUI:

```bash
python main.py --club "/path/to/clips"
```

Both read `/path/to/clips/brief.md` and write into that folder:

| File | What it is |
| --- | --- |
| `draft.mp4` | The assembled review cut. Original audio from the clips. Not written by `pick`. |
| `cuts.json` | Windows in playback order: source file, start, end, score. Forced highlights are marked. `whisper` is the model that produced the words. |
| `scores.json` | Every candidate window, which signals fired, and which were kept. `signals.whisper` is that same model. `signals.cut_timeline` points at `timeline.json`. The window list is a fallback fill, not a pick list of group numbers. |
| `transcript.md` | Readable transcript with times. Written by `pick` / `--transcript-only`. For naming quotes. |
| `transcript.json` | The same transcript per file, with segment and word times. `whisper` is the model that was run. |
| `timeline.json` | Full transcript plus per-second motion, and a suggested `in` / `out` on each stretch of speech. This is the cut editor. |
| `timeline.md` | The same moments, readable. |

Clips sit **in that folder**, not in subfolders. `mp4`, `mov`, `m4v`, `mkv`,
`avi`, and `webm` are read. A previous `draft.mp4` is not treated as a source.

Useful flags:

```bash
python -m modules.club ./clips --whisper-model large-v3
python -m modules.club ./clips --whisper-model small
python -m modules.club ./clips --no-whisper
python -m modules.club ./clips --brief ~/briefs/saturday.md --out ~/Desktop/out
python -m modules.club ./clips --dry-run
python -m modules.club pick ./clips
python -m modules.club ./clips --transcript-only
```

`--dry-run` writes the JSON and does not encode `draft.mp4`.
`--no-whisper` ranks on audio peaks and motion only.
`--whisper-model` defaults to `medium`. Use `large-v3` when a take is still
muddy, or `small` for a faster pass. `base` is still accepted and is the
model that mis-heard short cues. Caption language is English.
The model name is written on `transcript.json` (`whisper`), `timeline.json`
(`whisper`), `cuts.json` (`whisper`), and `scores.json` (`signals.whisper`).
Assemble reuses `transcript.json` when it is already there, so that name
is the model that actually heard the clips. Delete `transcript.json` to
hear them again with the model on the command line.
`pick` and `--transcript-only` write `transcript.md`, `transcript.json`,
`timeline.md`, and `timeline.json`, and do not assemble a draft.
See [Two-step talking pick](#two-step-talking-pick).
Exit code `0` means a draft plan with at least one cut was written.
Exit code `2` means the folder or the brief could not be read.

## What the ranker uses

In order of preference:

1. **Local Whisper** (`openai-whisper`, default model `medium`). Transcripts stay
   on this machine. The first run downloads the model weights into the local
   cache. Keyword hits and speech coverage come from that transcript.
   `timeline.json` is the full transcript beside per-second motion, which is
   what a cut is chosen from. The scored windows below are the fallback fill.
2. **Audio peaks** (`modules/audio/audio_peaks.py`) for impacts, calls, and
   crowd noise.
3. **Motion**, sampled with ffmpeg at a few frames a second. The frames are
   compared and discarded. Nothing is saved as a still.

`hype` weights peaks and motion higher. `talking` weights speech and keywords
higher. Neither style synthesizes media.

### Lisa, 30 seconds

Re-run from this branch (`cursor/club-montage-ops-8c48`). Put the clips in
one folder with this `brief.md`. Do not copy `CAPTION_SIZE` or
`CAPTION_POSITION` from the Dan draft.

```text
LENGTH: 30s
STYLE: talking
BRAND: tier1
TITLE: Private lesson with Coach John Wang
TITLE_ACCENT: John Wang
FONT: Inter
KEYWORDS: lesson, clinic
NOTES: Prefer windows where the coach is speaking. Do not use a spoken line as the title.
```

Do not copy `COLORS`, `CAPTION_POSITION: bottom`, or `CAPTION_SIZE: 42`
from an older draft. The title is bold white with no stroke. Captions
are one line, white, with a black stroke, sized to about 75% of the
frame width and still smaller than the title. A wide phrase is the
next cue. A call to action, if you add one, matches the title at a
slightly smaller size.
Add `CTA:` only when an end line is wanted.

```bash
python -m modules.club "/path/to/lisa-clips"
```

On `draft.mp4`: the title is the who/what line, bold white, horizontally
centered, with no stroke and no bar. Captions sit with their center at
two-fifths of the frame height up from the bottom, below the middle,
horizontally centered, white with a black
stroke, one line at a time, covering about 75% of the width on a short
cue. A wide phrase is the next cue. Those words are the Whisper cues,
not the title.
`cuts.json` lists them under `captions.cues`.

If the folder is one lesson mic, level is not used to hide a clip. Check
`mic_preference` in `cuts.json`. `mode` is `same_session` when every file
is within 18 dB of the loudest, and `mixed` when a file is further down.
A mixed file stays, with its score multiplied by 0.85.

### Quiet takes in a talking folder

When `STYLE` is `talking` and the folder has more than one clip, each file's
mean volume is read with ffmpeg `volumedetect`. Fill order after any
must-include is high speech, then motion inside those talking windows,
then brief keywords, then audio energy as a weak tie-break. A window
that is mostly silence or a still is not used to fill what the
must-includes left open.

A same-session lesson (the quietest file is within **18 dB** of the
loudest) is not penalised. A file **18 dB or more** below the loudest —
a phone next to a boom — stays in the ranker and its windows are scored
at **0.85**. Clear speech on that file can still beat a loud silent
window. Nothing is dropped for level. The choice is written on
`cuts.json` and `scores.json` under `mic_preference` (`mode`, `soft`,
`factor`, `volumes_db`). `dropped` stays empty.

This is level only. It does not look at the picture, so it will not spot
a phone zoom that is about as loud as the mic. A folder with one clip
is unchanged. If a phone zoom is as loud as the lesson mic and you do
not want it, leave that file out of the folder by hand.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**ffmpeg** is required to measure motion and to cut and join. A `ffmpeg` already
on `PATH` is used. Otherwise the `imageio-ffmpeg` package in
`requirements.txt` supplies a binary, staged on first use by
`modules/media/ffmpeg_tools.py`. On a Mac, `brew install ffmpeg` is the
straightforward way to get `ffmpeg` and `ffprobe` together.

Whisper is already in `requirements.txt` (`openai-whisper`). Club scoring does
not need the object-detection stack; the rest of the requirements file is the
desktop highlighter.

## How Social / Grok Bot runs a job

The bot stages files, then this repo does the cut. Suggested clone on the
club Mac:

```text
~/Desktop/Marketing/Grok Bot Work/VideoHighlighter
```

Do not encode until the person has answered both of these:

1. **LENGTH**, or **none**. `30s` and `20-35s` are targets. `none` means
   the named moments are the whole cut. Do not invent a runtime.
2. **Must-include moments or quotes.** Their words. These become
   `MUST_INCLUDE`. The transcript is the list they pick from. It is not
   a numbered group, and `scores.json` windows are not a pick list.

For each montage:

1. Download the Drive clips into an empty working folder. Do not send those
   files out for analysis.
2. Hear the folder before the brief is finished, so the quotes are real
   lines. From the clone:

   ```bash
   source .venv/bin/activate
   python -m modules.club pick "/path/to/staged/clips"
   ```

   Default Whisper is `medium`. The name is on `transcript.json` and
   `timeline.json`. A muddier take:

   ```bash
   python -m modules.club pick "/path/to/staged/clips" --whisper-model large-v3
   ```

   A faster pass is `--whisper-model small`. Delete `transcript.json`
   before assemble if the model has to change; assemble will not re-hear
   a transcript that is already there.

3. Read `timeline.json`. Each file has `segments` (the full transcript,
   with word times) and `motion` / `motion_norm` (one sample per second).
   `moments` are the proposed cuts:

   - `in` starts before the words, so the line is not clipped.
   - `out` starts after the words. If the picture is still moving, `out`
     waits until that motion drops, then keeps about 2.5 seconds of quiet
     (between 2 and 3 seconds when the clip has the room). It does not
     cross the next line. `cut_mid_action` is true when the next line or
     the end of the clip forced a cut while the picture was still moving.
     `short_silence` is true when there was not 2 seconds of quiet to keep.
   - When the same line is in the folder more than once, `take` is
     `doing` on the occurrence with more motion after the words, and
     `saying` on the others. `prefer` is true only on the doing take.
     Keep that one when the coach says the line and then does it. Keep
     the saying take only when the person asked for the words without
     the action.

4. Write `brief.md` in that same folder. `STYLE: talking`, the LENGTH
   answer, `MUST_INCLUDE` for the quotes, and `INCLUDE_WINDOWS` for the
   ranges copied from the moments (`file start-end`, seconds). Keywords
   come from the person requesting the montage. Title and brand as usual.
5. Encode:

   ```bash
   python -m modules.club "/path/to/staged/clips"
   ```

6. Read `draft.mp4`, `cuts.json`, `timeline.json`, and `scores.json` back
   from that folder.
7. If the draft is the wrong length or the wrong moment, change the brief
   or add another real clip and run again. Do not fill a gap with generated
   footage, a generated voice, or a generated track. Do not switch the cut
   to a group number from the score grid.

`NOTES` is there so the editor can see the request next to the cut list. The
ranker does not treat it as a generation prompt. `timeline.md` is the same
moments in a form a person can scan while they name quotes.

## Desktop app and localhost

Automated club runs stay on the CLI. The original app is still here.

**Qt desktop** (timeline viewer and the full highlighter):

```bash
python main.py
```

That opens a window. It does not serve a page.

**Localhost engine** (FastAPI sidecar the web UI talks to), loopback only:

```bash
python -m sidecar.server --port 8756
```

Then open <http://127.0.0.1:8756/docs>.

The React/Tauri shell lives in `frontend/`. On Windows, `frontend/dev.ps1`
starts it (`pnpm tauri dev`) and points it at the sidecar. Details are in
[frontend/README.md](frontend/README.md). Club jobs for the bot do not need
that UI.
