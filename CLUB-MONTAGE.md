# Club montages

This fork builds review drafts for Woodinville Sports Club and Tier 1 club
montages (tennis, golf, APL). A folder of **real clips** plus a `brief.md`
becomes a ranked cut list and one assembled file.

The club path cuts windows out of those clips and joins the windows. When
the brief includes a title, subtitle, or call to action, that type is drawn
on the joined cut. It does not generate B-roll, faces, voices, or songs. If
a moment is missing, the fix is another real clip or a different in/out in
`cuts.json`.

The desktop highlighter is unchanged. Automated runs use the CLI below.

## brief.md

Put `brief.md` in the clips folder. Length and style are required. The
type fields are optional: leave them out and the run stays a highlights cut.

```text
LENGTH: 20-35s
STYLE: hype
TITLE: Match day
SUBTITLE: Woodinville Tennis
COLORS: #1B4D3E, #F4E8C1
FONT: DejaVu Sans
CTA: See you Saturday
KEYWORDS: ace, rally, birdie
NOTES: Prefer the last shot of the point. Keep the original audio.
```

| Field | Required | Meaning |
| --- | --- | --- |
| `LENGTH` | yes | Finished draft length. `20-35s` is a range. `30s` is exact. |
| `STYLE` | yes | `hype` or `talking`. |
| `KEYWORDS` | no | Words to boost when local Whisper hears them. Commas or bullets. |
| `NOTES` | no | Copied into `cuts.json` for the editor. Not a prompt. |
| `TITLE` | no | Opening title, drawn on the first seconds of the real cut. |
| `SUBTITLE` | no | Second line on the title card, and the lower third. |
| `COLORS` | no | Hex colors. The first is the bar, the second is the type. |
| `FONT` | no | A `.ttf` / `.otf` path, or a font name installed on the machine. |
| `CTA` | no | End-card line, drawn on the closing seconds of the real cut. |

`hype` favours audio peaks and motion, and plays the strongest windows first.
`talking` favours speech and keywords, and plays clips in name order, then
time order, so a conversation stays in sequence.

Heading form works too (`## LENGTH` on its own line, value underneath).
A colon inside `NOTES` stays part of the note.

Keywords are supplied per brief. The app has no built-in sport category list.

## On-screen type

`TITLE`, `SUBTITLE`, and `CTA` are burned onto `draft.mp4` after the clips
are joined. The pass does not add frames and does not replace the picture.
A bar in the brief's first color sits behind the words; the second color is
the type. One color keeps that bar and chooses black or white type for
contrast. With no `COLORS`, the bar is near-black and the type is white.

Timing on a cut long enough for three bands (about two and a half seconds
and up):

- Title card: `TITLE` and `SUBTITLE` over the opening.
- Lower third: `SUBTITLE`, or `TITLE` when there is no subtitle, through the middle.
- End card: `CTA` over the close.

Shorter cuts drop the lower third so the title and the call to action do
not stack. `FONT` is a file path when that file exists, otherwise a font
name (`Arial`, `DejaVu Sans`, `Helvetica`). If the font cannot be found, a
bold sans already on the machine is used. If drawing fails, `draft.mp4`
stays the unbranded cut and `cuts.json` records `brand.error`.

`brand.requested` is false when the brief has no title, subtitle, or call
to action. Colors or a font alone do not start the pass.

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
