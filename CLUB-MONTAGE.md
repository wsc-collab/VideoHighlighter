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
in `cuts.json`. Caption lines are the transcript. They are not copy written
in the brief.

The desktop highlighter is unchanged. Automated runs use the CLI below.

## brief.md

Put `brief.md` in the clips folder. Length and style are required. The
type fields are optional: leave them out and the run stays a highlights cut.

```text
LENGTH: 20s
STYLE: talking
BRAND: tier1
TITLE: Private lesson with Coach John Wang
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
The title is bold white, horizontally centered, with no stroke and no
plate. A call to action, when the brief sets one, uses that same look at
a slightly smaller size. Captions stay one line: white with a black
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

Leave `CAPTION_SIZE` and `CAPTION_POSITION` out. The defaults put captions
in the center, one line at a time, about four words. The point size
scales with the frame so a short cue covers about 75% of the width, and
stays under the title. A line that would be wider than the frame is the
next caption cue. A brief copied from an earlier draft that still says
`CAPTION_POSITION: bottom` or `CAPTION_SIZE: 42` keeps that old look.
Delete those two lines. Set them only to override:

```text
CAPTION_SIZE: 72
CAPTION_POSITION: bottom
```

| Field | Required | Meaning |
| --- | --- | --- |
| `LENGTH` | yes | Finished draft length. `20-35s` is a range. `30s` is exact. |
| `STYLE` | yes | `talking` for this pack. `hype` only ranks louder, busier windows and does not burn speech captions. |
| `KEYWORDS` | no | Words to boost when local Whisper hears them while choosing windows. Not caption text. |
| `NOTES` | no | Copied into `cuts.json` for the editor. Not a prompt and not a caption. |
| `TITLE` | no | Who and what the video is, about the first 2–3 seconds. Bold white, horizontally centered, no stroke, no plate. Not a line from the transcript. |
| `SUBTITLE` | no | Optional second line with the title. Same bold white, no stroke. |
| `COLORS` | no | Ignored for talking type. On `hype`, the first hex is the bar and the second is the type. |
| `BRAND` | no | `tier1` (golf and tennis Tier 1), `wsc`, or `bsc`. Picks the default face when `FONT` is omitted. |
| `FONT` | no | A `.ttf` / `.otf` path, or a font name. Default is Inter for `tier1`, `wsc`, and `bsc`. |
| `CTA` | no | Optional close, about the last 2–3 seconds. Same as the title (bold white, centered, no stroke, no plate) at a slightly smaller size. Leave it out for no end line. |
| `CAPTION_COLOR` | no | Drawn as white or black. Default is white. A cream or orange value is drawn as white. |
| `CAPTION_STROKE` | no | Outline. Default is black behind white type, or white behind black type. Width example: `#000000 3`. |
| `CAPTION_SIZE` | no | Caption point size. Default scales with frame width so a short cue covers about 75% of the width, and stays smaller than the title. |
| `CAPTION_POSITION` | no | `center` (default), `bottom`, or `top`. `middle` is the same as `center`. |
| `CAPTION_FONT` | no | Caption face. Falls back to `FONT`. |

`talking` favours speech and keywords, and plays clips in name order, then
time order, so a conversation stays in sequence. `hype` favours audio peaks
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
  type is bold, white, and horizontally centered, with no stroke and no
  plate. On a cut under about eight seconds that opening shrinks so it
  cannot cover the captions. Install `Inter-Bold.otf` next to Inter when
  you want the real bold face; otherwise the regular face is used.
- **Captions** for speech whose middle falls between the title and the
  call to action. Default position is the center of the frame, about four
  words, one line on screen. The size scales with the frame so a short
  cue covers about 75% of the width, and stays smaller than the title.
  Type is white with a black stroke and no box. A phrase that would
  overflow is the next timed cue, not a second line. The engine is
  local Whisper: `faster-whisper` when that package is installed,
  otherwise `openai-whisper` from `requirements.txt`. No caption is sent
  to a paid API. `--no-whisper` leaves the captions off and does not
  invent lines to fill them.
- **Call to action** from `CTA`, only when that line is set, for about
  the last 2.5 seconds. Same as the title — bold white, horizontally
  centered, no stroke, no plate — at a slightly smaller size.

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
| `draft.mp4` | The assembled review cut. Original audio from the clips. |
| `cuts.json` | Windows in playback order: source file, start, end, score. |
| `scores.json` | Every candidate window, which signals fired, and which were kept. |

Clips sit **in that folder**, not in subfolders. `mp4`, `mov`, `m4v`, `mkv`,
`avi`, and `webm` are read. A previous `draft.mp4` is not treated as a source.

Useful flags:

```bash
python -m modules.club ./clips --whisper-model tiny
python -m modules.club ./clips --no-whisper
python -m modules.club ./clips --brief ~/briefs/saturday.md --out ~/Desktop/out
python -m modules.club ./clips --dry-run
```

`--dry-run` writes the JSON and does not encode `draft.mp4`.
`--no-whisper` ranks on audio peaks and motion only.
Exit code `0` means a draft plan with at least one cut was written.
Exit code `2` means the folder or the brief could not be read.

## What the ranker uses

In order of preference:

1. **Local Whisper** (`openai-whisper`, default model `base`). Transcripts stay
   on this machine. The first run downloads the model weights into the local
   cache. Keyword hits and speech coverage come from that transcript.
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
centered, with no stroke and no bar. Captions sit in the center, white
with a black stroke, one line at a time, covering about 75% of the
width on a short cue. A wide phrase is the next cue. Those words are
the Whisper cues, not the title.
`cuts.json` lists them under `captions.cues`.

If the folder also has a quiet phone zoom of a mic'd take, check
`mic_preference` in `cuts.json`. A file 12 dB or more under the loudest
clip is left out. A face-zoom that is about as loud as the mic is not
detected; leave that file out of the folder by hand.

### Quiet takes in a talking folder

When `STYLE` is `talking` and the folder has more than one clip, each file's
mean volume is read with ffmpeg `volumedetect`. A clip **12 dB or more**
below the loudest file is left out of the cut. That is the usual gap between
a mic'd take and a quiet phone recording of the same moment. The choice is
written on `cuts.json` and `scores.json` under `mic_preference` (`dropped`,
`volumes_db`).

This is level only. It does not look at the picture, so it will not spot a
phone zoom that is about as loud as the mic. Two files within 12 dB both
stay. A folder with one clip always stays. If Lisa's phone file is a zoom
of a mic'd take and the levels are close, leave that phone file out of the
folder by hand before the run.

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

## How Grok Bot runs a job

The bot stages files, then this repo does the cut. Suggested clone on the
club Mac:

```text
~/Desktop/Marketing/Grok Bot Work/VideoHighlighter
```

For each montage:

1. Download the Drive clips into an empty working folder. Do not send those
   files out for analysis.
2. Write `brief.md` into that same folder (`LENGTH`, `STYLE`, `KEYWORDS`,
   `NOTES`). Keywords come from the person requesting the montage.
3. From the clone:

   ```bash
   source .venv/bin/activate
   python -m modules.club "/path/to/staged/clips"
   ```

4. Read `draft.mp4`, `cuts.json`, and `scores.json` back from that folder.
5. If the draft is the wrong length or the wrong moment, change the brief or
   add another real clip and run again. Do not fill a gap with generated
   footage, a generated voice, or a generated track.

`NOTES` is there so the editor can see the request next to the cut list. The
ranker does not treat it as a generation prompt.

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
