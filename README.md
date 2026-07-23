# MediaInfo Metadata Harvest Tool

A zsh utility that recursively scans a directory for audiovisual files and writes a
CSV of selected technical metadata, read via the [MediaInfo](https://mediaarea.net/en/MediaInfo)
CLI. It is built to help prepare audio and video assets for digital collections
processing by producing a single inventory file with format, duration, 
resolution, bit rate, and related characteristics. This was built on and for macOS.

The tool ships in two parts:

- **`MediaInfoHarvest.zsh`** — the core command-line script that does the work.
- **`HarvestLauncher.command`** — an interactive, double-click front end that
  prompts for the input and output paths, for operators who prefer not to type flags.

---

## What it captures

For every time-based file found, one CSV row is written with these columns:

| Column | MediaInfo template | Description |
| --- | --- | --- |
| `filename` | (file path) | Full path of the source file |
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
| `audio_bit_rate_mode` | `Audio;%BitRate_Mode%` | CBR / VBR |
| `audio_bit_rate_value` | `Audio;%BitRate/String%` | Audio bit rate with units |
| `audio_muxing_mode` | `Audio;%MuxingMode%` | Muxing mode |
| `menu_format` | `Menu;%Format%` | Menu/chapter format |

---

## Requirements

- **zsh** — the core script uses zsh specific syntax (`read -r "?prompt"`, the `:h`
  modifier). zsh is the default shell on modern macOS.
- **MediaInfo CLI** — install with Homebrew: `brew install mediainfo`.
- **macOS** is assumed (the `.command` launcher is a macOS convenience). The core
  script runs anywhere zsh and `mediainfo` are available.

---

## Installation

```sh
git clone https://github.com/<your-org>/mediainfo-metadata-harvest.git
cd mediainfo-metadata-harvest
chmod +x MediaInfoHarvest.zsh HarvestLauncher.command
```

Both files must remain in the same directory: the launcher changes into its own
folder and calls `./MediaInfoHarvest.zsh` by relative path.

---

## Usage

### Interactive (double-click)

Double-click **`HarvestLauncher.command`** in Finder. A Terminal window opens and
prompts for:

1. the input directory (full path), and
2. the output CSV file (full path, including `.csv`).

### Command line

```sh
./MediaInfoHarvest.zsh -i /Volumes/Media -o ~/Desktop/output.csv
```

| Option | Description |
| --- | --- |
| `-i` | Input directory (required) |
| `-o` | Output CSV file, including `.csv` (required) |
| `-h` | Show help |

---

## Output

A CSV at the path you specify, with the header row and columns listed above. Each
value is wrapped in double quotes. 

---

## How files are selected

The script walks the input directory recursively (`find … -type f`) and, for each
file, reads the General duration. Files with an empty or zero duration are skipped.
This filters out still images, text sidecars, and other non-time-based files
automatically, so only audio and video assets end up in the CSV.

---

## Repository structure

```
mediainfo-metadata-harvest/
├── MediaInfoHarvest.zsh      # Core CLI harvester
├── HarvestLauncher.command   # Interactive double-click launcher
├── README.md                 # This file
├── CHANGELOG.md              # Version history
├── LICENSE                   # License
└── .gitignore                # Ignores macOS cruft, editor files, generated CSVs
```

---

## Notes and limitations

- **One `mediainfo` call per field.** Each file is probed roughly two dozen times,
  once per column. This is simple and reliable but slow on large batches.
- **Strict error handling.** The core script runs with `set -o errexit`, so a file
  that causes `mediainfo` to exit non-zero (e.g. a corrupt or unreadable file) can
  abort the whole run mid-batch rather than skipping that file.
- **CSV quoting is basic.** Fields are wrapped in quotes but embedded double quotes
  in a filename are not escaped, which could misalign a row. Most preservation
  filenames avoid this, but it is worth noting.
- **Full paths in `filename`.** The column stores the complete path, not just the
  base filename.


## License

See [LICENSE](LICENSE).
