# MediaInfo Metadata Harvest Tool

A small cross-platform utility that recursively scans a directory of media files
and writes a CSV of selected technical metadata, read via the
[MediaInfo](https://mediaarea.net/en/MediaInfo) CLI. It is built to help prepare
audio/video assets for digital collections processing by producing a single,
spreadsheet-friendly inventory of format, duration, resolution, bit rate, and
related characteristics -- and to flag multi-part item sequences that mix
audio-bearing and video-only segments, before that turns into a surprise during
access-file assembly (e.g. `run_overnight_batch.sh`-style workflows).

It is meant for **partner-submitted files**, which don't follow California
Revealed's folder-naming conventions, so it does no naming check. Its audio-only
sibling, `mediainfo_sound_harvest.py`, is for files already in the finished
`<marc_code>/<object_identifier>/<filename>` layout.

> **Upgrading from 2.x:** v3.0.0 is a breaking change. The `filename` column used
> to hold the full path; it is now `full_file_path`, and a new `filename` column
> holds just the file name. Anything that reads the CSV by column name needs
> updating. See [CHANGELOG.md](CHANGELOG.md).

The tool ships in three parts:

- **`mediainfo_harvest.py`** — the core script that does the work. Pure Python,
  runs on Windows, macOS, and Linux.
- **`HarvestLauncher.command`** — an interactive, double-click front end for
  macOS.
- **`HarvestLauncher.bat`** — an interactive, double-click front end for
  Windows.

---

## What it captures

For every time-based file found, one CSV row is written with these columns:

| Column | MediaInfo field | Description |
| --- | --- | --- |
| `full_file_path` | (file path) | Full path of the source file |
| `marc_code` | (folder position) | Name of the folder two levels above the file |
| `object_identifier` | (folder position) | Name of the folder the file is in |
| `filename` | (file name) | The file name with all of its extensions, e.g. `calauem_006941_access.HD.mp4` |
| `general_duration` | `General;%Duration/String3%` | Container duration (HH:MM:SS.mmm) |
| `video_duration` | `Video;%Duration/String3%` | Video track duration |
| `audio_duration` | `Audio;%Duration/String3%` | Audio track duration |
| `width` | `Video;%Width%` | Frame width in pixels |
| `height` | `Video;%Height%` | Frame height in pixels |
| `frame_rate` | `Video;%FrameRate/String%` | Frame rate with units |
| `video_format` | `Video;%Format%` | Video codec/format |
| `video_format_version` | `Video;%Format_Version%` | Format version |
| `video_standard` | `Video;%Standard%` | Broadcast standard (e.g. NTSC/PAL) |
| `video_color_space` | `Video;%ColorSpace%` | Color space |
| `video_bit_depth` | `Video;%BitDepth%` | Bit depth |
| `video_scan_type` | `Video;%ScanType%` | Progressive or interlaced |
| `video_bit_rate_mode` | `Video;%BitRate_Mode%` | CBR / VBR |
| `video_bit_rate_value` | `Video;%BitRate/String%` | Video bit rate with units |
| `video_maximum_bit_rate` | `Video;%BitRate_Maximum/String%` | Max video bit rate; falls back to `General;%OverallBitRate/String%` when unavailable |
| `aspect_ratio` | `Video;%DisplayAspectRatio/String%` | Display aspect ratio |
| `audio_format` | `Audio;%Format%` | Audio codec/format |
| `audio_format_info` | `Audio;%Format_AdditionalFeatures%` | Additional format features |
| `audio_channels` | `Audio;%Channel(s)%` | Channel count |
| `audio_sampling_rate` | `Audio;%SamplingRate%` | Sampling rate in Hz, e.g. `96000` |
| `audio_bit_depth` | `Audio;%BitDepth%` | Bit depth, e.g. `24`; blank for lossy codecs |
| `audio_bit_rate_mode` | `Audio;%BitRate_Mode%` | CBR / VBR |
| `audio_bit_rate_value` | `Audio;%BitRate/String%` | Audio bit rate with units |
| `audio_muxing_mode` | `Audio;%MuxingMode%` | Muxing mode |
| `menu_format` | `Menu;%Format%` | Menu/chapter format |
| `has_audio_stream` | `General;%AudioCount%` > 0 | `yes` / `no` — whether the file has any audio track at all |
| `audio_stream_count` | `General;%AudioCount%` | Number of audio streams |
| `segment_group` | (derived from filename) | Item prefix, if the filename matches the segment naming convention (see below) |
| `segment_number` | (derived from filename) | Segment number within that item, if matched |

---

## Path columns

`marc_code`, `object_identifier` and `filename` describe where a file sits, in
the finished layout:

```
.../<marc_code>/<object_identifier>/<filename>
e.g. .../casfjazz/casfjazz_000395/casfjazz_000395_f00003_access.m4a
```

- They come from **folder positions only** (`path.parent.parent.name`,
  `path.parent.name`, `path.name`). They are never parsed from the filename,
  because filenames can and do disagree with their object folder.
- They are worked out **per file**, not from the input folder, so a run over
  several partner sets at once gives each file its own `marc_code`.
- Nothing is validated. If a file sits somewhere else, you get whatever the two
  folders above it are called. A file at the top of a drive gets blanks.
- Windows and macOS paths both split correctly.

---

## Sidecar exclusion

`.vtt`, `.md5` and `.xml` files are always excluded, with no prompt, before any
MediaInfo call. Matching is on the **final extension only**, case-insensitive:

- `casfjazz_000368_f00002_access.m4a.md5` is excluded;
- `X_ACCESS.VTT` and `x_PBCore.Xml` are excluded;
- a media file that merely has `xml` or `md5` in its name, such as
  `xml_md5_000001_f01_access.m4a`, is not.

The end-of-run summary reports a per-extension count, e.g.
`Excluded sidecars: 777 .vtt, 1554 .md5, 159 .xml`.

---

## Silent-segment detection

Multi-part captures (e.g. `calauem_006976_f01_prsv.VOB`, `_f02_`, `_f03_`, ...)
sometimes have one or more segments with no audio track at all -- usually a
capture/transfer artifact rather than an intentional silence. If that goes
unnoticed until an access-file concat step, it can produce sync problems that
are much harder to diagnose after the fact.

This tool catches it at harvest time:

1. Every file's filename is checked against a segment-naming pattern. By
   default this matches `<prefix>_f<N>_prsv.<ext>` (e.g.
   `calauem_006976_f01_prsv.VOB` → prefix `calauem_006976`, segment `01`).
   Override with `--segment-pattern` if your naming convention differs (must
   supply a regex with named groups `prefix` and `segnum`).
2. Files sharing the same prefix are grouped.
3. If a group contains **both** audio-bearing and video-only segments, it's
   written to **`flagged_items.log`** (next to the output CSV) with a
   per-segment breakdown, so you can review it before running an assembly
   script like `run_overnight_batch.sh`.

Every file also gets its own `has_audio_stream` / `audio_stream_count` columns
in the CSV regardless of whether it matched the naming pattern, so you can spot
check individual files too.

---

## Requirements

- **Python 3.7+** — pre-installed on most macOS and Linux systems; on Windows,
  install from [python.org](https://www.python.org/downloads/windows/) and
  check "Add python.exe to PATH" during setup. No third-party Python packages
  (see `requirements.txt`).
- **MediaInfo CLI** — must be on your system PATH, or pointed to with
  `--mediainfo PATH`.
  - macOS: `brew install mediainfo`
  - Windows: download the **CLI** package (not just the GUI) from
    [mediaarea.net](https://mediaarea.net/en/MediaInfo/Download/Windows) and
    make sure the install location is on your PATH.
  - Linux: available via most package managers, e.g. `apt install mediainfo`.

---

## Installation

```sh
git clone https://github.com/<your-org>/mediainfo-metadata-harvest.git
cd mediainfo-metadata-harvest
chmod +x mediainfo_harvest.py HarvestLauncher.command   # macOS/Linux only
python3 mediainfo_harvest.py --selftest                 # optional: verify the install
```

All files should remain in the same directory.

---

## Usage

### Interactive (double-click)

- **macOS:** double-click `HarvestLauncher.command`.
- **Windows:** double-click `HarvestLauncher.bat`.

A terminal window opens and prompts for:

1. the input directory (full path), and
2. the output CSV file (full path, including `.csv`).

Bad answers (blank, a folder that doesn't exist, a folder where a `.csv` file
is expected) are explained and re-prompted. Tip: dragging a folder or file into
the terminal window pastes its exact path; quoted paths and macOS Terminal's
backslash-escaped paths (`CAR\ 066`) are both handled.

### Command line

```sh
python3 mediainfo_harvest.py -i /Volumes/Media -o ~/Desktop/output.csv
```

On Windows (Command Prompt or PowerShell):

```
python mediainfo_harvest.py -i D:\Media -o C:\Users\you\Desktop\output.csv
```

Any input not given as a flag is prompted for. For unattended runs, add
`--no-prompt` so a missing flag fails with exit code 2 instead of waiting for
someone to type.

| Option | Description |
| --- | --- |
| `-i`, `--input` | Input directory, searched recursively. Prompted for if omitted |
| `-o`, `--output` | Output CSV file, including `.csv`. Prompted for if omitted. Logs are written next to it |
| `--segment-pattern` | Custom regex (named groups `prefix`, `segnum`) for grouping segmented items |
| `--mediainfo PATH` | Path to the `mediainfo` CLI if it isn't on PATH |
| `--no-prompt` | Never prompt; exit 2 if `-i` / `-o` are missing |
| `--selftest` | Run the built-in self-test and exit |
| `--version` | Show the version |
| `-h`, `--help` | Show help |

### Exit codes

For gating a larger workflow. These match `mediainfo_sound_harvest.py`.

| Code | Meaning |
| --- | --- |
| `0` | Harvest finished; nothing needs review |
| `1` | Harvest finished, but needs review: MediaInfo/read errors, flagged sequences, or zero rows written |
| `2` | Usage / input error (bad flag value, missing folder, no input available) |
| `3` | MediaInfo CLI not found |
| `4` | `--selftest` failed |
| `130` | Interrupted (Ctrl+C) |

The Sound tool also uses `5` for folder-naming mismatches. This tool never
returns `5`, since it does no naming check.

---

## Output

Files land next to your specified output path:

- **`<output>.csv`** — one row per time-based media file, RFC-4180-quoted.
- **`flagged_items.log`** — sequences with mixed audio-bearing / video-only
  segments, for review. Says "None found." if there's nothing to flag.
- **`harvest_errors.log`** — only created if MediaInfo failed to read one or
  more files (e.g. corrupt files) or a folder couldn't be read; lists which and
  why. The run continues past these rather than aborting.

The end-of-run summary shows rows written, the per-extension sidecar count, and
how many files were skipped as not time-based media, so the numbers add up to
the files found.

---

## How files are selected

The script walks the input directory recursively, in sorted path order. A file
ends up in the CSV only if:

1. it is not a `.vtt`, `.md5` or `.xml` sidecar (dropped by filename, before
   MediaInfo is called); and
2. MediaInfo reports a non-zero General duration. Files with an empty or zero
   duration (still images, text files, and so on) are skipped, so only
   audio/video assets end up in the CSV.

---

## Self-test

```sh
python3 mediainfo_harvest.py --selftest
```

Unit tests run anywhere and emulate MediaInfo's `--Inform` output. They cover:
path columns from macOS-style and Windows-style paths; two partner sets in one
run; a filename that disagrees with its folder; sidecar exclusion (each
extension, mixed case); the new audio columns; the multi-stream regression
(including the last field of each category); exit codes; and prompting.

If the MediaInfo CLI is found, integration tests also run against real
MediaInfo. They generate their own test media (WAV files, and a Matroska file
with two audio tracks) using only the standard library. If MediaInfo isn't
found, those tests are reported as skipped, not passed.

---

## Repository structure

```
mediainfo-metadata-harvest/
├── mediainfo_harvest.py      # Core cross-platform harvester (+ --selftest)
├── HarvestLauncher.command   # Interactive double-click launcher (macOS)
├── HarvestLauncher.bat       # Interactive double-click launcher (Windows)
├── requirements.txt          # No third-party packages; notes the MediaInfo CLI
├── README.md                 # This file
├── CHANGELOG.md              # Version history
├── LICENSE                   # MIT License
├── .gitattributes            # Keeps .bat as CRLF and .command/.py as LF
└── .gitignore                # Ignores OS cruft, editor files, generated CSVs/logs
```

---

## Notes and limitations

- **One MediaInfo call per category, not per field.** Each file is probed four
  times (General/Video/Audio/Menu) rather than ~24, which is considerably
  faster on large batches.
- **First stream only.** If a file has multiple audio or video streams,
  only the first is reported per category (this avoids embedding stray newlines
  inside a CSV cell). `audio_stream_count` still shows how many audio streams
  there are.
- **Segment grouping is filename-based**, using the `--segment-pattern` regex,
  and works across subdirectories (grouping only looks at each file's
  basename, not its folder). Files that don't match the pattern aren't grouped
  or flagged, but still get their own `has_audio_stream` value in the CSV.
- **`harvest_errors.log` is only written when there are errors**, so a log left
  over from an earlier run in the same folder can be stale. The exit code is the
  reliable signal.
- **AppleDouble `._*` files and `.DS_Store`** are not excluded; they go to
  MediaInfo like any other file. (`mediainfo_sound_harvest.py` skips them.)

## Possible improvements

- Read General/Video/Audio/Menu in a single combined `--Inform` template
  (further reduces process-launch overhead).
- Configurable duration threshold for "effectively silent" segments (e.g. a
  segment with audio present but near-zero level), rather than binary
  has-audio-stream detection.

---

## License

See [LICENSE](LICENSE).
