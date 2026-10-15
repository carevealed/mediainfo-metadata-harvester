# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-10-15

**Breaking change.** The `filename` CSV column used to hold the full path. It
now holds just the file name, and the full path moved to the new
`full_file_path` column. Anything that reads the CSV by column name needs
updating. The exit codes are also new (see "Changed"), which matters to any
script that checked the old exit status.

The three features below were built and tested in `mediainfo_sound_harvest.py`
v1.0.0 (the Sound-type variant) and are brought over here with the same logic.

### Added
- Path columns, right after the renamed first column: `marc_code` (the folder
  two levels above the file), `object_identifier` (the folder the file is in)
  and `filename` (the file name with all of its extensions). They come from
  folder positions only, never from the filename, and are worked out per file,
  so a run over several partner sets at once gives each file its own
  `marc_code`. Windows and macOS paths both split correctly.
- CSV columns `audio_sampling_rate` (`Audio;%SamplingRate%`, e.g. `96000`) and
  `audio_bit_depth` (`Audio;%BitDepth%`, e.g. `24`; blank for lossy codecs),
  right after `audio_channels`. `Duration/String3` stays the last field of the
  Audio category.
- Sidecar exclusion: `.vtt`, `.md5` and `.xml` files are always excluded, with
  no prompt, before any MediaInfo call. Matching is on the final extension
  only, case-insensitive, so `x_access.m4a.md5` is excluded but a media file
  that merely has `xml` or `md5` in its name is not. The end-of-run summary
  reports a per-extension count, e.g.
  `Excluded sidecars: 777 .vtt, 1554 .md5, 159 .xml`.
- Exit codes for workflow gating, matching `mediainfo_sound_harvest.py`:
  0 ok, 1 finished but needs review, 2 usage/input error, 3 MediaInfo not
  found, 4 self-test failed, 130 interrupted. Code 5 (folder-naming
  mismatches) is Sound-only; this tool never returns it, because it is used on
  partner-submitted files that don't follow CA-R's folder naming.
- `--selftest`: unit tests with an emulated MediaInfo, plus integration tests
  against real MediaInfo when the CLI is found. The integration tests generate
  their own test media (WAV files and a two-audio-track Matroska file) using
  only the standard library.
- `--no-prompt` (fail with exit 2 instead of prompting), `--mediainfo PATH`,
  and `--version`.
- `requirements.txt` (no third-party packages) and `.gitattributes`.

### Changed
- BREAKING: the `filename` column is renamed `full_file_path`; a new
  `filename` column holds the file name only (see above).
- Exit status. A run now exits 1 when it finished but needs review (flagged
  sequences, unreadable files or folders, or zero rows written). Previously a
  finished run always exited 0, and input errors exited 1. Input errors now
  exit 2 and a missing MediaInfo exits 3.
- A missing, blank or invalid path at the prompt is explained and re-prompted
  instead of ending the run. A folder given where a `.csv` file is expected is
  rejected, and a missing `.csv` extension is added.
- The progress bar counts the files sent to MediaInfo, so sidecars no longer
  count toward it.
- The end-of-run summary reads "Rows written: N of M files found" (was "files
  scanned"), and adds the sidecar counts and the number of files skipped as not
  time-based media, so the numbers add up.
- Files are processed in sorted path order, so output is deterministic.
  Unreadable subfolders are recorded in `harvest_errors.log`.
- An empty input folder now writes a header-only CSV and the logs, and exits
  1. Previously it printed "No files found." and wrote nothing.
- Path prompts accept macOS Terminal drag-and-drop escaping (`CAR\ 066`) as
  well as quoted paths.
- Status output no longer crashes when stdout is redirected to a non-UTF-8
  file on Windows.
- `.gitignore` also ignores `__pycache__/` and `*.pyc`.

### Fixed (launchers)
- `HarvestLauncher.command` uses bash's `read -p`. The previous launcher used
  zsh's `read "?prompt"` syntax under a `#!/bin/bash` shebang. bash rejects
  that, so the window didn't pause for Enter.
- `HarvestLauncher.bat` tries the `py` launcher before `python`, which avoids
  the Microsoft Store placeholder. Both launchers now pass arguments through
  and return the script's exit code.

### Unchanged
- The cross-platform Python approach, one MediaInfo call per category with the
  trailing `\n` in each `--Inform` template (the 2.0.1 multi-stream fix),
  csv-module quoting, the progress bar, continuing past unreadable files, and
  `flagged_items.log` (same name, format and flagging rule) and
  `harvest_errors.log`.

## [2.0.1] - 2026-08-13

### Fixed
- `mediainfo_harvest.py`: fixed field corruption on files with multiple
  streams of a kind (e.g. a DVD-sourced `.iso` with two audio tracks).
  `--Inform` templates now include an explicit `\n` so MediaInfo inserts a
  newline between repeated stream blocks; previously, with no separator
  requested, MediaInfo concatenated stream 2's fields directly onto the end
  of stream 1's last field (e.g. `video_duration` of `00:09:47.500` would
  read as `00:09:47.500720`, with `720` actually being stream 2's Width).
  Found via a real-world 685-file test batch. Only the last field in a
  category was affected; all other fields were unaffected regardless of
  stream count.

## [2.0.0] - 2026-08-12

### Added
- `mediainfo_harvest.py`: cross-platform Python rewrite (Windows/macOS/Linux)
  replacing the zsh-only `MediaInfoHarvest.zsh`.
- `HarvestLauncher.bat`: interactive double-click launcher for Windows,
  mirroring `HarvestLauncher.command` on macOS.
- Silent-segment detection: `has_audio_stream` and `audio_stream_count`
  columns per file, plus `flagged_items.log`, which groups files by item
  prefix (via a configurable `--segment-pattern` regex) and flags any
  sequence that mixes audio-bearing and video-only segments -- catching the
  issue that `run_overnight_batch.sh` has to work around, at harvest time
  instead of during access-file assembly.
- `harvest_errors.log`: lists any files MediaInfo failed to read.

### Changed
- One MediaInfo call per category (General/Video/Audio/Menu) instead of one
  call per field (~4 calls per file instead of ~24) — substantially faster
  on large batches.
- CSV writing now uses Python's `csv` module (RFC-4180 quoting), fixing a
  latent bug where an embedded double quote in a filename could misalign a
  row.
- The run now continues past files MediaInfo can't read instead of aborting
  the whole batch (previously `set -o errexit` in the zsh script would kill
  the run on the first such file).

### Removed
- `MediaInfoHarvest.zsh` and the original `HarvestLauncher.command` (macOS
  zsh version) — superseded by `mediainfo_harvest.py` and the updated
  launchers. See git history if you need the old versions.

## [1.1.0] - 2026-07-20

### Added
- `MediaInfoHarvest.zsh`: live progress bar showing percent complete and a
  running file count (e.g. `[########------------]  40% (102/256)`).

### Changed
- `MediaInfoHarvest.zsh`: header text now reads "Reading files in:" instead of
  "Processing files in:".

## [1.0.0] - 2026-07-20

### Added
- Initial release.
- `MediaInfoHarvest.zsh` — core CLI that recursively harvests technical metadata
  from time-based media files into a CSV, skipping files with no duration.
- `HarvestLauncher.command` — interactive double-click launcher that prompts for
  input and output paths and validates them before running the harvest.
