# handy-batch-transcribe

A small command-line tool to transcribe audio/video files in batch, reusing the
local speech model that the [Handy](https://github.com/cjpais/Handy) dictation
app already downloaded. Handy itself only does live push-to-talk dictation and
has no file-upload or batch mode; this fills that gap without changing Handy.

Everything runs offline on-device. The Parakeet model is read straight from
Handy's app-support folder, so there is nothing large to download.

## Two ways to use it

- **Desktop app** (`app.py`) — a window where you drop files in, choose move/copy,
  and get a searchable library. See "Desktop app" below.
- **Command line** (`transcribe`) — batch a folder from the terminal. See "Usage".

## Desktop app

```bash
~/.venvs/handy-transcribe/bin/python ~/Documents/GitHub/handy-batch-transcribe/app.py
```

Or build a double-clickable app once: `./build_app.sh` → `dist/Handy Transcribe.app`.

In the window you can:

- **Drop** audio/video from anywhere (Finder, Downloads), or click **Add files…**.
- Choose **Move** (remove the original) or **Copy** (leave it) — with a remembered default.
- **Transcribe**; each file produces a `.md` + `.srt` kept permanently in the library.
- Decide what happens to the **audio**: by default it auto-deletes after N days (set in
  Settings). After a batch, flip **Save audio** on any file (or **Save all**) to move it
  into a Finder-visible `Audio Archive/` and keep it.
- **Library tab**: search by filename or by words spoken, filter by week (or This/Last
  week), open the transcript/SRT, reveal in Finder, or play the saved audio.

The library lives at `~/Transcribe Library/` by default (`Transcripts/`, `Audio Archive/`,
`Audio Pending/`, plus `library.db`). Change its location in Settings. App preferences are
stored in `~/Library/Application Support/HandyTranscribe/`.

Expired audio is swept when the app launches and after each batch; there's also a
**Clean expired** button. There is no background daemon.

## What it does

For each input file it writes, next to the source (or into `--out DIR`):

- `<name>.md`  a readable transcript with small YAML frontmatter (vault-friendly)
- `<name>.srt` timestamped subtitle cues

Handles common audio and video containers (m4a, mp3, mp4, mov, wav, flac, …) via
ffmpeg. Recordings longer than 45 s are split into windows cut at silent gaps
(Parakeet drops speech on long single passes and crashes past ~400 s), and each
window's timestamps are merged back to absolute time.

## Requirements

- macOS with the **Handy** app installed and its `parakeet-tdt-0.6b-v3` model
  downloaded (Settings → Models in Handy).
- **ffmpeg** on PATH (`brew install ffmpeg`).
- The dedicated venv at `~/.venvs/handy-transcribe` (see Install).

## Install

```bash
/opt/homebrew/bin/python3.12 -m venv ~/.venvs/handy-transcribe
~/.venvs/handy-transcribe/bin/pip install -r requirements.txt
```

A wrapper is installed at `~/.local/bin/transcribe`. Make sure `~/.local/bin` is
on your PATH (it already is if you use the `clean-transcription` tool).

## Usage

```bash
transcribe                      # transcribe everything in ~/Transcribe-Inbox
transcribe meeting.m4a          # one file -> meeting.md + meeting.srt beside it
transcribe lecture.mp4 voice/   # mix of files and folders
transcribe --out ~/Transcripts recordings/   # write outputs elsewhere
transcribe --force notes.m4a    # redo even if notes.md already exists
transcribe --model /path/to/model-dir ...    # override model location
```

Re-runs are idempotent: a file whose `.md` already exists is skipped unless
`--force` is given. A drop folder `~/Transcribe-Inbox` is created on first run
with no arguments, so you can just drop files there and run `transcribe`.

## Language note

The Parakeet model covers English plus ~24 European languages. It does **not**
transcribe Hebrew, Greek, or other non-European scripts. For those, a Whisper
model would need to be added (out of scope for this tool today).

## How it works

1. `ffmpeg` decodes each input to 16 kHz mono PCM WAV.
2. `onnx-asr` loads Handy's Parakeet ONNX files
   (`~/Library/Application Support/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8`)
   and transcribes with token-level timestamps.
3. Tokens are grouped into caption-sized cues for the `.srt`; the same text is
   reflowed into paragraphs for the `.md`.

No VAD model or network access is needed at runtime.

## Uninstall / kill path

This tool runs only when you invoke it (no daemon, no background job). To remove:

```bash
rm ~/.local/bin/transcribe
rm -rf ~/.venvs/handy-transcribe
rm -rf ~/Documents/GitHub/handy-batch-transcribe
# optional: rm -rf ~/Transcribe-Inbox
```

Handy and its models are untouched by any of the above.

## License

Licensed under the **PolyForm Noncommercial License 1.0.0** — free to use, modify,
and share for any **noncommercial** purpose (personal, research, education, religious,
or nonprofit/charitable use). Commercial use is not granted by this license. See
[LICENSE](LICENSE) for the full terms.

This is an independent companion tool for the [Handy](https://github.com/cjpais/Handy)
dictation app; it is not affiliated with or endorsed by Handy. It does not redistribute
any speech model: the NVIDIA Parakeet model is read at runtime from your own local Handy
installation and remains under NVIDIA's own license. Python dependencies
(`onnx-asr`, `onnxruntime`, `pywebview`) are under their respective licenses.
