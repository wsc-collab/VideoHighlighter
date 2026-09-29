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
LENGTH: 20-35s
STYLE: talking
TITLE: Match day
CTA: See you Saturday
FONT: DejaVu Sans
COLORS: #1B4D3E, #F4E8C1
CAPTION_COLOR: #FFFFFF
CAPTION_STROKE: #000000 3
KEYWORDS: lesson, clinic
NOTES: Keep the coach's answer. Do not write caption copy here.
```

Leave `CAPTION_SIZE` and `CAPTION_POSITION` out. The defaults put captions
in the center, about four words a line, at a size that follows the frame
and is at least 64pt. A brief copied from the earlier Dan draft that still
says `CAPTION_POSITION: bottom` or `CAPTION_SIZE: 42` keeps that old look.
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
| `TITLE` | no | Opening title, about the first 2–3 seconds. Centered on a solid plate. |
| `SUBTITLE` | no | Optional second line on that same centered plate. |
| `COLORS` | no | Hex colors. The first is the solid title/CTA plate, the second is that type. |
| `FONT` | no | A `.ttf` / `.otf` path, or a font name installed on the machine. |
| `CTA` | no | End card, about the last 2–3 seconds. Centered on a solid plate. |
| `CAPTION_COLOR` | no | Caption fill. Default is white, or the second `COLORS` value when that is set. |
| `CAPTION_STROKE` | no | Outline color and optional width, e.g. `#000000 3`. Default is black, 3. |
| `CAPTION_SIZE` | no | Caption point size. Default follows the frame height and is at least 64. |
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
  seconds. The type is centered on both axes over a solid plate (fully
  opaque, not a translucent wash). On a cut under about eight seconds the
  plate shrinks so it cannot cover the captions.
- **Captions** for speech whose middle falls between the title and the
  call to action. Default position is the center of the frame, about four
  words a line, at least 64pt (larger on a 1080 frame). The engine is
  local Whisper: `faster-whisper` when that package is installed,
  otherwise `openai-whisper` from `requirements.txt`. No caption is sent
  to a paid API. `--no-whisper` leaves the captions off and does not
  invent lines to fill them.
- **Call to action** from `CTA` for about the last 2.5 seconds, on the
  same kind of centered solid plate as the title.

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
TITLE: Match day
CTA: See you Saturday
FONT: DejaVu Sans
COLORS: #1B4D3E, #F4E8C1
KEYWORDS: lesson, clinic
NOTES: Keep the coach's answer. Do not write caption copy here.
```

```bash
python -m modules.club "/path/to/lisa-clips"
```

On `draft.mp4`: the title and the call to action are centered on a solid
plate (the picture does not show through the bar). Captions sit in the
center of the frame, about four words a line, larger than 42pt. The words
are still the Whisper cues. `cuts.json` lists them under `captions.cues`.

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
