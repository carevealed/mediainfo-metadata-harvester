#!/usr/bin/env python3
"""
MediaInfo Metadata Harvest Tool (cross-platform)
==================================================

Recursively scans an input directory for time-based media files and writes a
CSV of selected technical metadata, read via the MediaInfo CLI. Files with no
General duration are skipped, so still images and other non-AV files are
excluded automatically.

A file ends up in the CSV only if:
  - it is not a sidecar file (.vtt captions, .md5 checksums and .xml metadata
    are always excluded, no prompt, by final extension -- see
    ALWAYS_EXCLUDED_EXTENSIONS); and
  - MediaInfo reports a non-zero General duration (time-based media).

New in v3.0.0 (see CHANGELOG.md -- BREAKING: the "filename" column changed):
  - Path columns: "full_file_path" (the old "filename" column), then
    "marc_code" (folder two levels above the file), "object_identifier"
    (the folder the file is in) and "filename" (the file name with all of its
    extensions). Taken from folder positions only, never parsed from the
    filename, and worked out per file, so one run over several partner sets
    gives each file its own marc_code.
  - Audio columns "audio_sampling_rate" and "audio_bit_depth".
  - Sidecar exclusion with a per-extension count in the end-of-run summary.
  - Exit codes for workflow gating (same scheme as mediainfo_sound_harvest.py).
  - --selftest, --no-prompt, --mediainfo, --version.

Unchanged from v2.0.1: one MediaInfo call per category with the trailing "\\n"
in each --Inform template (multi-stream fix), csv-module quoting, progress
bar, continuing past unreadable files (harvest_errors.log), and the
segment-sequence check (flagged_items.log).

Usage:
    mediainfo_harvest.py                          (prompts for anything missing)
    mediainfo_harvest.py -i INPUT_DIRECTORY -o OUTPUT_FILE.csv
    mediainfo_harvest.py --selftest

Exit codes (for workflow gating):
    0  harvest finished; nothing needs review
    1  harvest finished, but needs review: MediaInfo/read errors, flagged
       sequences, or zero rows written
    2  usage / input error (bad flag value, missing folder, no input available)
    3  MediaInfo CLI not found
    4  --selftest failed
  130  interrupted (Ctrl+C)
  (mediainfo_sound_harvest.py also uses 5 for folder-naming mismatches. This
  tool never returns 5: it is used on partner-submitted files, which don't
  follow CA-R's folder naming, so it does no naming check.)

Requires: Python 3.7+, MediaInfo CLI (https://mediaarea.net/en/MediaInfo).
No third-party Python packages.
"""

import argparse
import csv
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path, PurePosixPath, PureWindowsPath

__version__ = "3.0.0"
TOOL_NAME = "MediaInfo Metadata Harvest Tool"

EXIT_OK = 0
EXIT_REVIEW = 1
EXIT_USAGE = 2
EXIT_NO_MEDIAINFO = 3
EXIT_SELFTEST_FAILED = 4
EXIT_INTERRUPTED = 130

# Sidecars that are never media: captions, checksums, metadata (e.g. PBCore).
# Matched on the FINAL extension only (Path.suffix), case-insensitive, by
# filename alone -- before any MediaInfo call. "x_access.m4a.md5" is excluded;
# a media file that merely contains "xml" or "md5" in its name is not.
ALWAYS_EXCLUDED_EXTENSIONS = (".vtt", ".md5", ".xml")

# ---- Default naming convention for segmented items -------------------------
# Matches e.g. "calauem_006976_f01_prsv.VOB" -> prefix="calauem_006976", segnum="01"
# Override with --segment-pattern if your shop uses a different convention.
# (Used only for flagged_items.log -- the path columns never use it.)
DEFAULT_SEGMENT_PATTERN = re.compile(
    r"^(?P<prefix>.+)_f(?P<segnum>\d+)_prsv\.[^.]+$", re.IGNORECASE
)

# ---- MediaInfo fields, grouped by category (one CLI call per category) -----
# Order matters: it determines the order values are unpacked in below.
# Duration/String3 is deliberately LAST in VIDEO_FIELDS and AUDIO_FIELDS: the
# last field of a category is the one the v2.0.0 multi-stream bug corrupted,
# so keeping it last means the self-test exercises exactly that case.
GENERAL_FIELDS = ["Duration", "Duration/String3", "OverallBitRate/String", "AudioCount"]
VIDEO_FIELDS = [
    "Width", "Height", "FrameRate/String", "Format", "Format_Version", "Standard",
    "ColorSpace", "BitDepth", "ScanType", "BitRate_Mode", "BitRate/String",
    "BitRate_Maximum/String", "DisplayAspectRatio/String", "Duration/String3",
]
AUDIO_FIELDS = [
    "Format", "Format_AdditionalFeatures", "Channel(s)", "SamplingRate", "BitDepth",
    "BitRate_Mode", "BitRate/String", "MuxingMode", "Duration/String3",
]
MENU_FIELDS = ["Format"]

CSV_COLUMNS = [
    "full_file_path", "marc_code", "object_identifier", "filename",
    "general_duration", "video_duration", "audio_duration", "width",
    "height", "frame_rate", "video_format", "video_format_version", "video_standard",
    "video_color_space", "video_bit_depth", "video_scan_type", "video_bit_rate_mode",
    "video_bit_rate_value", "video_maximum_bit_rate", "aspect_ratio", "audio_format",
    "audio_format_info", "audio_channels", "audio_sampling_rate", "audio_bit_depth",
    "audio_bit_rate_mode", "audio_bit_rate_value", "audio_muxing_mode", "menu_format",
    "has_audio_stream", "audio_stream_count", "segment_group", "segment_number",
]

FLAGGED_LOG = "flagged_items.log"
ERRORS_LOG = "harvest_errors.log"


class UsageError(Exception):
    """Bad or missing input. Maps to EXIT_USAGE."""


# =============================================================================
# Small helpers
# =============================================================================

def configure_stdio():
    # Unattended runs on Windows often redirect stdout to a file with a
    # cp1252 encoding, where printing the status emoji would crash the run at
    # the very end. Replace unencodable characters instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def find_mediainfo(explicit=None):
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.is_file() else None
    return shutil.which("mediainfo") or shutil.which("mediainfo.exe")


MEDIAINFO_MISSING_MSG = (
    "ERROR: mediainfo CLI not found.\n"
    "Install it from https://mediaarea.net/en/MediaInfo and make sure it's on\n"
    "your PATH (macOS: brew install mediainfo; on Windows you need the 'CLI'\n"
    "package, not just the GUI), or point to it with --mediainfo PATH."
)


def build_template(kind, fields):
    # The trailing literal "\n" is required: MediaInfo does not insert a
    # newline between repeated template applications for multi-stream files
    # (e.g. a DVD .iso with two audio tracks) unless the template asks for
    # one. Without it, stream 2's fields get concatenated directly onto the
    # end of stream 1's last field with no delimiter at all -- e.g. a
    # duration of "00:09:47.500" becomes "00:09:47.500720" when the next
    # stream's Width value runs straight into it.
    return f"{kind};" + "|".join(f"%{f}%" for f in fields) + "\\n"


def parse_inform_output(stdout, nfields):
    """Keep only the first line (= first stream) and split it into fields."""
    first_line = stdout.split("\n", 1)[0].strip()
    parts = first_line.split("|")
    if len(parts) < nfields:
        parts += [""] * (nfields - len(parts))
    return parts[:nfields]


def run_inform(mediainfo_exe, filepath, kind, fields, _run=subprocess.run):
    """Run one MediaInfo --Inform call for a category and return the field
    values (strings) for the FIRST stream of that kind, in `fields` order, or
    None if MediaInfo failed. Multi-stream files report stream 1 only, which
    also avoids embedding raw newlines inside a CSV cell."""
    try:
        result = _run(
            [mediainfo_exe, f"--Inform={build_template(kind, fields)}", str(filepath)],
            capture_output=True, text=True, errors="replace", timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return parse_inform_output(result.stdout, len(fields))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def print_progress(current, total, width=20):
    pct = int(current * 100 / total) if total else 0
    filled = int(pct * width / 100)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {pct:3d}% ({current}/{total})", end="", flush=True)


def match_segment(filename, pattern):
    m = pattern.match(filename)
    if not m:
        return None, None
    return m.group("prefix"), m.group("segnum")


def path_columns(path):
    """Columns derived from where a file sits:
         .../<marc_code>/<object_identifier>/<filename>
       e.g. .../casfjazz/casfjazz_000395/casfjazz_000395_f00003_access.m4a

    Values come from folder POSITIONS only -- never parsed from the filename,
    which can and does disagree with its object folder. Worked out per file
    (not from the input folder), so a run over several partner sets at once
    gives each file its own marc_code. Uses the path's own parts, so Windows
    and macOS paths both split correctly. A level that doesn't exist (file at
    a drive root) is blank. `filename` is the name with ALL its extensions
    (e.g. calauem_006941_access.HD.mp4)."""
    return {
        "full_file_path": str(path),
        "marc_code": path.parent.parent.name,
        "object_identifier": path.parent.name,
        "filename": path.name,
    }


def is_sidecar(path):
    """True for .vtt / .md5 / .xml, by final extension, case-insensitive."""
    return path.suffix.lower() in ALWAYS_EXCLUDED_EXTENSIONS


def clean_path_input(raw):
    raw = raw.strip()
    # Drag-and-drop on Windows (and some Mac apps) wraps paths in quotes.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    # Drag-and-drop into macOS Terminal backslash-escapes spaces and
    # punctuation instead ("CAR\ 066"). Backslashes are path separators on
    # Windows, so only undo this elsewhere.
    if os.name != "nt" and "\\" in raw:
        raw = re.sub(r"\\(.)", r"\1", raw)
    return raw


def check_input_dir(path):
    if not path.is_dir():
        raise UsageError(f"Input directory does not exist:\n  {path}")
    return path


def check_output_csv(path):
    if path.is_dir():
        raise UsageError(f"Output must be a .csv file path, not a folder:\n  {path}")
    if path.suffix.lower() != ".csv":
        path = path.with_name(path.name + ".csv")
        print(f"(Added .csv -- output will be {path.name})")
    if not path.parent.is_dir():
        raise UsageError(f"Output directory does not exist:\n  {path.parent}")
    return path


# =============================================================================
# Settings: CLI flags first, interactive prompts for anything missing
# =============================================================================

def ask(label):
    try:
        return input(label)
    except EOFError:
        raise UsageError(
            "No interactive input available. Supply -i and -o on the command "
            "line (or use --no-prompt)."
        )


def prompt_until_valid(label, convert):
    while True:
        raw = ask(label)
        try:
            return convert(raw)
        except (ValueError, UsageError) as e:
            print(f"  {e}")


def _required_path(check):
    def convert(raw):
        if not raw.strip():
            raise ValueError("A path is required.")
        return check(Path(clean_path_input(raw)).expanduser())
    return convert


def resolve_settings(args):
    interactive = not args.no_prompt
    s = {}
    header_shown = [False]

    def header():
        if not header_shown[0]:
            print(f"{TOOL_NAME} v{__version__}")
            print("-" * (len(TOOL_NAME) + len(__version__) + 2))
            header_shown[0] = True

    # 1. Input directory
    if args.input is not None:
        s["input"] = check_input_dir(Path(args.input).expanduser())
    elif interactive:
        header()
        s["input"] = prompt_until_valid(
            "Input directory (drag folder here or paste path): ", _required_path(check_input_dir)
        )
    else:
        raise UsageError("--input is required when --no-prompt is used.")

    # 2. Output CSV
    if args.output is not None:
        s["output"] = check_output_csv(Path(args.output).expanduser())
    elif interactive:
        header()
        s["output"] = prompt_until_valid(
            "Output CSV file (include .csv): ", _required_path(check_output_csv)
        )
    else:
        raise UsageError("--output is required when --no-prompt is used.")

    # Segment pattern (optional; has a default, so it is never prompted for)
    if args.segment_pattern:
        try:
            pat = re.compile(args.segment_pattern, re.IGNORECASE)
        except re.error as e:
            raise UsageError(f"--segment-pattern is not a valid regex: {e}")
        if "prefix" not in pat.groupindex or "segnum" not in pat.groupindex:
            raise UsageError("--segment-pattern must define named groups 'prefix' and 'segnum'.")
        s["segment_pattern"] = pat
    else:
        s["segment_pattern"] = DEFAULT_SEGMENT_PATTERN

    if header_shown[0]:
        print()
    return s


# =============================================================================
# Harvest
# =============================================================================

class HarvestResult:
    def __init__(self):
        self.rows = []
        self.errors = []        # (path, reason)
        self.sidecars = []      # Paths excluded by extension, before any MediaInfo call
        self.non_media = 0      # probed, but MediaInfo reports no duration
        self.groups = {}        # prefix -> [segment dicts]
        self.total_found = 0

    @property
    def flagged(self):
        """Sequences that mix audio-bearing and video-only segments."""
        return {
            prefix: segs for prefix, segs in self.groups.items()
            if any(s["has_audio"] for s in segs) and any(not s["has_audio"] for s in segs)
        }


def collect_files(input_path):
    files, errors = [], []

    def onerror(exc):
        errors.append((getattr(exc, "filename", "") or str(input_path),
                       f"Could not read folder: {exc.strerror or exc}"))

    for root, dirs, names in os.walk(str(input_path), onerror=onerror):
        dirs.sort(key=str.lower)
        for n in names:
            files.append(Path(root) / n)
    files.sort(key=lambda p: str(p).lower())
    return files, errors


def harvest(input_path, mediainfo_exe, seg_pattern, show_progress=True, _run=subprocess.run):
    res = HarvestResult()

    files, walk_errors = collect_files(input_path)
    res.errors.extend(walk_errors)
    res.total_found = len(files)

    # Sidecars are dropped by filename alone, before any MediaInfo call.
    to_probe = []
    for f in files:
        if is_sidecar(f):
            res.sidecars.append(f)
        else:
            to_probe.append(f)

    for idx, f in enumerate(to_probe, start=1):
        if show_progress:
            print_progress(idx, len(to_probe))

        general = run_inform(mediainfo_exe, f, "General", GENERAL_FIELDS, _run=_run)
        if general is None:
            res.errors.append((str(f), "MediaInfo General call failed"))
            continue

        raw_dur_ms, gdur, overall_br, audio_count = general
        if to_float(raw_dur_ms) <= 0:
            res.non_media += 1   # not time-based media (image, text, etc.) -- skipped, as before
            continue

        video = run_inform(mediainfo_exe, f, "Video", VIDEO_FIELDS, _run=_run) or [""] * len(VIDEO_FIELDS)
        audio = run_inform(mediainfo_exe, f, "Audio", AUDIO_FIELDS, _run=_run) or [""] * len(AUDIO_FIELDS)
        menu = run_inform(mediainfo_exe, f, "Menu", MENU_FIELDS, _run=_run) or [""] * len(MENU_FIELDS)

        (vwidth, vheight, vframerate, vformat, vformatver, vstandard, vcolorspace,
         vbitdepth, vscantype, vbrmode, vbrval, vmaxbr, aspect, vdur) = video
        (aformat, aformatinfo, achannels, arate, adepth,
         abrmode, abrval, amuxmode, adur) = audio

        if not vmaxbr:
            vmaxbr = overall_br

        audio_count_int = to_int(audio_count)
        has_audio = audio_count_int > 0
        prefix, segnum = match_segment(f.name, seg_pattern)

        res.rows.append({
            **path_columns(f),
            "general_duration": gdur,
            "video_duration": vdur,
            "audio_duration": adur,
            "width": vwidth,
            "height": vheight,
            "frame_rate": vframerate,
            "video_format": vformat,
            "video_format_version": vformatver,
            "video_standard": vstandard,
            "video_color_space": vcolorspace,
            "video_bit_depth": vbitdepth,
            "video_scan_type": vscantype,
            "video_bit_rate_mode": vbrmode,
            "video_bit_rate_value": vbrval,
            "video_maximum_bit_rate": vmaxbr,
            "aspect_ratio": aspect,
            "audio_format": aformat,
            "audio_format_info": aformatinfo,
            "audio_channels": achannels,
            "audio_sampling_rate": arate,
            "audio_bit_depth": adepth,
            "audio_bit_rate_mode": abrmode,
            "audio_bit_rate_value": abrval,
            "audio_muxing_mode": amuxmode,
            "menu_format": menu[0],
            "has_audio_stream": "yes" if has_audio else "no",
            "audio_stream_count": audio_count_int,
            "segment_group": prefix or "",
            "segment_number": segnum or "",
        })

        if prefix:
            res.groups.setdefault(prefix, []).append({
                "segnum": segnum, "filename": f.name, "has_audio": has_audio, "duration": gdur,
            })

    if show_progress and to_probe:
        print()  # move off the progress-bar line
    return res


# =============================================================================
# Output
# =============================================================================

def write_csv(rows, fp):
    writer = csv.DictWriter(fp, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(rows)


def write_outputs(res, s):
    out_dir = s["output"].parent
    paths = {
        "csv": s["output"],
        "flagged": out_dir / FLAGGED_LOG,
        "errors": out_dir / ERRORS_LOG,
    }

    with open(paths["csv"], "w", newline="", encoding="utf-8") as fp:
        write_csv(res.rows, fp)

    # ---- Flagged sequences: groups mixing audio-bearing and video-only segments
    flagged = res.flagged
    with open(paths["flagged"], "w", encoding="utf-8") as ff:
        ff.write("Flagged sequences -- mixed audio / video-only segments\n")
        ff.write(f"Generated from harvest of: {s['input']}\n\n")
        if not flagged:
            ff.write("None found.\n")
        else:
            for prefix in sorted(flagged):
                segs = sorted(flagged[prefix], key=lambda x: int(x["segnum"]))
                ff.write(f"=== {prefix} ({len(segs)} segments) ===\n")
                for seg in segs:
                    tag = "HAS AUDIO" if seg["has_audio"] else "VIDEO-ONLY (silent)"
                    ff.write(f"  [f{seg['segnum']}] {seg['filename']} -> {tag}, duration={seg['duration']}\n")
                ff.write("\n")

    # ---- Error log for files MediaInfo couldn't read (only written if needed)
    if res.errors:
        with open(paths["errors"], "w", encoding="utf-8") as ef:
            ef.write("Files skipped due to MediaInfo errors during harvest:\n\n")
            for fn, reason in res.errors:
                ef.write(f"{fn} -- {reason}\n")
    return paths


def sidecar_counts(res):
    counts = {}
    for path in res.sidecars:
        ext = path.suffix.lower()
        counts[ext] = counts.get(ext, 0) + 1
    return counts


def sidecar_summary_line(res):
    counts = sidecar_counts(res)
    return "Excluded sidecars: " + ", ".join(
        f"{counts.get(e, 0)} {e}" for e in ALWAYS_EXCLUDED_EXTENSIONS)


def exit_code_for(res):
    return EXIT_REVIEW if (res.errors or res.flagged or not res.rows) else EXIT_OK


def print_summary(res, paths):
    flagged = res.flagged
    print("\u2705 Metadata harvest complete")
    print(f"Output saved to:\n  {paths['csv']}")
    print(f"Rows written: {len(res.rows)} of {res.total_found} files found")
    print(f"  {sidecar_summary_line(res)}")
    print(f"  Skipped as not time-based media: {res.non_media}")
    print(f"Segment sequence report: {paths['flagged']}")
    if flagged:
        print(f"\u26a0\ufe0f  {len(flagged)} sequence(s) flagged with mixed audio/video-only segments -- review before assembly.")
    if res.errors:
        print(f"\u26a0\ufe0f  {len(res.errors)} file(s)/folder(s) skipped due to errors -- see:\n  {paths['errors']}")
    if not res.rows:
        print("\u26a0\ufe0f  No rows written -- check the input folder.")


# =============================================================================
# Self-test
# =============================================================================

class _Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_mediainfo(streams, returncode=0):
    """Emulates MediaInfo --Inform: the template body is applied once per
    stream and the results are concatenated with NO separator, except where
    the template itself contains a literal \\n."""
    def _run(cmd, **_kw):
        body = cmd[1][len("--Inform="):].partition(";")[2]
        out = []
        for stream in streams:
            text = body
            for k, v in stream.items():
                text = text.replace(f"%{k}%", v)
            out.append(re.sub(r"%[^%]+%", "", text).replace("\\n", "\n"))
        return _Completed("".join(out) + "\n", returncode)
    return _run


def _emulated_mediainfo(specs, probed=None):
    """A per-file MediaInfo emulator for harvest(). `specs` maps a file NAME to
    {"General": [stream dicts], "Video": [...], "Audio": [...], "Menu": [...]},
    or to "ERROR" (MediaInfo exits non-zero). Unknown files give empty output,
    like a text file. Every probed file name is appended to `probed`."""
    def _run(cmd, **kw):
        kind = cmd[1][len("--Inform="):].partition(";")[0]
        name = Path(cmd[2]).name
        if probed is not None:
            probed.append(name)
        spec = specs.get(name)
        if spec == "ERROR":
            return _Completed("", 1)
        return _fake_mediainfo((spec or {}).get(kind, []))(cmd)
    return _run


def _spec(dur_ms, audio=(), video=(), overall="1 000 kb/s"):
    """Build an emulator spec for one file from its audio and video streams."""
    h, rem = divmod(int(dur_ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return {
        "General": [{"Duration": str(int(dur_ms)),
                     "Duration/String3": f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}",
                     "OverallBitRate/String": overall, "AudioCount": str(len(audio))}],
        "Audio": list(audio), "Video": list(video), "Menu": [],
    }


def _write_wav(path, seconds, rate=8000, channels=1, sampwidth=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(b"\x00" * sampwidth * channels * int(rate * seconds))


def _ebml(el_id, payload):
    return el_id.to_bytes((el_id.bit_length() + 7) // 8, "big") + b"\x01" + len(payload).to_bytes(7, "big") + payload


def _ebml_uint(el_id, v):
    return _ebml(el_id, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def _ebml_float(el_id, v):
    return _ebml(el_id, struct.pack(">d", v))


def _ebml_str(el_id, s):
    return _ebml(el_id, s.encode("ascii"))


def _write_mkv(path, seconds, audio_tracks=1, video=False, rate=8000):
    """Minimal Matroska file with N mono 16-bit PCM audio tracks (and
    optionally one video track header), written with the standard library
    only, so the self-test can exercise multi-stream and video-bearing files
    against the real MediaInfo CLI without needing ffmpeg."""
    tracks, audio_nums, n = [], [], 0
    if video:
        n += 1
        tracks.append(_ebml(0xAE, _ebml_uint(0xD7, n) + _ebml_uint(0x73C5, n) + _ebml_uint(0x83, 1)
                            + _ebml_str(0x86, "V_MPEG4/ISO/AVC")
                            + _ebml(0xE0, _ebml_uint(0xB0, 320) + _ebml_uint(0xBA, 240))))
    for _ in range(audio_tracks):
        n += 1
        audio_nums.append(n)
        tracks.append(_ebml(0xAE, _ebml_uint(0xD7, n) + _ebml_uint(0x73C5, n) + _ebml_uint(0x83, 2)
                            + _ebml_str(0x86, "A_PCM/INT/LIT")
                            + _ebml(0xE1, _ebml_float(0xB5, float(rate)) + _ebml_uint(0x9F, 1)
                                    + _ebml_uint(0x6264, 16))))
    blocks = b""
    for sec in range(int(seconds)):
        for tn in audio_nums:
            blocks += _ebml(0xA3, bytes([0x80 | tn]) + struct.pack(">h", sec * 1000) + b"\x80"
                            + b"\x00" * (rate * 2))
    ebml_header = _ebml(0x1A45DFA3, _ebml_uint(0x4286, 1) + _ebml_uint(0x42F7, 1) + _ebml_uint(0x42F2, 4)
                        + _ebml_uint(0x42F3, 8) + _ebml_str(0x4282, "matroska")
                        + _ebml_uint(0x4287, 4) + _ebml_uint(0x4285, 2))
    info = _ebml(0x1549A966, _ebml_uint(0x2AD7B1, 1000000) + _ebml_float(0x4489, seconds * 1000.0)
                 + _ebml_str(0x4D80, "selftest") + _ebml_str(0x5741, "selftest"))
    segment = _ebml(0x18538067, info + _ebml(0x1654AE6B, b"".join(tracks))
                    + _ebml(0x1F43B675, _ebml_uint(0xE7, 0) + blocks))
    Path(path).write_bytes(ebml_header + segment)


class _SelfTest:
    def __init__(self):
        self.passed = self.failed = self.skipped = 0

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))

    def skip(self, name, why):
        self.skipped += 1
        print(f"  SKIP  {name} -- {why}")


DUR_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


def _run_main(argv, inputs=None):
    """Call main() with captured output and, optionally, scripted input()
    answers. Returns (exit_code, stdout, stderr)."""
    import builtins
    import contextlib
    out, err = io.StringIO(), io.StringIO()
    real_input = builtins.input
    answers = list(inputs) if inputs is not None else None
    if answers is not None:
        def fake_input(prompt=""):
            if not answers:
                raise EOFError
            return answers.pop(0)
        builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    finally:
        builtins.input = real_input
    return code, out.getvalue(), err.getvalue()


def _unit_tests(t):
    print("Unit tests (no MediaInfo needed)")

    # --- Sidecar exclusion: final extension only, case-insensitive ----------
    for sidecar in ("x_access.vtt", "X_ACCESS.VTT", "x_prsv.Vtt",
                    "x_access.m4a.md5", "x_prsv.aiff.MD5",
                    "casfjazz_000368_f00002_access.m4a.md5",
                    "x_PBCore.xml", "x_prsv.XML", "y.Xml", "x.wav.xml"):
        t.check(f"sidecar excluded: {sidecar}", is_sidecar(Path(sidecar)))
    for media in ("xml_md5_000001_f01_access.m4a", "calauem_006941_access.HD.mp4",
                  "x_md5.wav", "vtt_xml_prsv.aiff", "x.xml.wav", "x.md5.m4a", "notes.txt"):
        t.check(f"not a sidecar (final extension decides): {media}", not is_sidecar(Path(media)))

    # --- Path columns: macOS-style and Windows-style paths -------------------
    mac = path_columns(PurePosixPath(
        "/Volumes/CAR_066_20T/casfjazz/casfjazz_000395/casfjazz_000395_f00003_access.m4a"))
    win = path_columns(PureWindowsPath(
        r"D:\CAR_066\casfjazz\casfjazz_000395\casfjazz_000395_f00003_access.m4a"))
    want = ("casfjazz", "casfjazz_000395", "casfjazz_000395_f00003_access.m4a")
    for label, cols in (("macOS", mac), ("Windows", win)):
        got = (cols["marc_code"], cols["object_identifier"], cols["filename"])
        t.check(f"path columns from a {label} path: marc_code / object_identifier / filename",
                got == want, f"got {got}")
    t.check("full_file_path keeps the whole path (macOS and Windows)",
            mac["full_file_path"].endswith("casfjazz/casfjazz_000395/casfjazz_000395_f00003_access.m4a")
            and win["full_file_path"].endswith(r"casfjazz\casfjazz_000395\casfjazz_000395_f00003_access.m4a"))
    multi_ext = path_columns(PurePosixPath("/V/cbbb/cbbb_000042/cbbb_000042_access.HD.mp4"))
    t.check("filename keeps all of its extensions", multi_ext["filename"] == "cbbb_000042_access.HD.mp4")
    odd = path_columns(PurePosixPath("/V/cbbb/cbbb_000042/zzzz_999999_f01_prsv.wav"))
    t.check("object_identifier comes from the folder even when the filename disagrees",
            odd["object_identifier"] == "cbbb_000042" and odd["marc_code"] == "cbbb")
    shallow = path_columns(PurePosixPath("/x_prsv.wav"))
    t.check("missing folder levels give blank marc_code / object_identifier",
            (shallow["marc_code"], shallow["object_identifier"]) == ("", ""))

    # --- Column layout ---------------------------------------------------------
    t.check("CSV starts full_file_path, marc_code, object_identifier, filename",
            CSV_COLUMNS[:4] == ["full_file_path", "marc_code", "object_identifier", "filename"])
    i = CSV_COLUMNS.index("audio_channels")
    t.check("audio_sampling_rate, audio_bit_depth come right after audio_channels",
            CSV_COLUMNS[i + 1:i + 3] == ["audio_sampling_rate", "audio_bit_depth"])
    t.check("CSV columns are unique", len(set(CSV_COLUMNS)) == len(CSV_COLUMNS))
    t.check("Duration/String3 is still the LAST field of AUDIO_FIELDS and VIDEO_FIELDS",
            AUDIO_FIELDS[-1] == "Duration/String3" and VIDEO_FIELDS[-1] == "Duration/String3")
    t.check("AUDIO_FIELDS has SamplingRate and BitDepth right after Channel(s)",
            AUDIO_FIELDS[AUDIO_FIELDS.index("Channel(s)") + 1:AUDIO_FIELDS.index("Channel(s)") + 3]
            == ["SamplingRate", "BitDepth"])

    # --- Multi-stream handling (the v2.0.1 fix) -------------------------------
    two_audio = [
        {"Format": "PCM", "Channel(s)": "1", "SamplingRate": "96000", "BitDepth": "24",
         "Duration/String3": "00:09:47.500"},
        {"Format": "AAC", "Channel(s)": "2", "SamplingRate": "48000", "BitDepth": "",
         "Duration/String3": "00:09:47.480"},
    ]
    two_video = [
        {"Width": "720", "Height": "480", "Format": "MPEG Video", "Duration/String3": "00:09:47.500"},
        {"Width": "704", "Height": "480", "Format": "AVC", "Duration/String3": "00:09:47.480"},
    ]
    t.check("template ends with a literal \\n", build_template("Audio", AUDIO_FIELDS).endswith("\\n"))
    vals = run_inform("mediainfo", "x", "Audio", AUDIO_FIELDS, _run=_fake_mediainfo(two_audio))
    t.check("multi-stream audio: first stream's last field is not corrupted",
            vals is not None and vals[-1] == "00:09:47.500", f"got {vals and vals[-1]!r}")
    t.check("multi-stream audio: first stream's values reported (incl. sampling rate, bit depth)",
            vals is not None and vals[0] == "PCM" and vals[3] == "96000" and vals[4] == "24", f"got {vals!r}")
    vvals = run_inform("mediainfo", "x", "Video", VIDEO_FIELDS, _run=_fake_mediainfo(two_video))
    t.check("multi-stream video: first stream's last field is not corrupted",
            vvals is not None and vvals[-1] == "00:09:47.500" and vvals[0] == "720", f"got {vvals!r}")
    # Sanity-check the emulator itself: without the \n, it reproduces the
    # v2.0.0 corruption, so the tests above are meaningful.
    fake = _fake_mediainfo(two_audio)
    old_template = "Audio;" + "|".join(f"%{f}%" for f in AUDIO_FIELDS)
    old = parse_inform_output(fake(["mediainfo", f"--Inform={old_template}", "x"]).stdout, len(AUDIO_FIELDS))
    t.check("emulator reproduces the v2.0.0 bug without \\n",
            old[-1].startswith("00:09:47.500") and old[-1] != "00:09:47.500", f"got {old[-1]!r}")
    t.check("short output padded to field count",
            run_inform("m", "x", "Audio", AUDIO_FIELDS, _run=lambda *a, **k: _Completed("PCM|\n")) ==
            ["PCM"] + [""] * (len(AUDIO_FIELDS) - 1))
    t.check("non-zero MediaInfo exit -> None (logged as error)",
            run_inform("m", "x", "General", GENERAL_FIELDS, _run=_fake_mediainfo([{}], returncode=1)) is None)

    buf = io.StringIO()
    write_csv([dict({c: "" for c in CSV_COLUMNS}, full_file_path='/x/say "hi", ok_prsv.wav')], buf)
    back = list(csv.DictReader(io.StringIO(buf.getvalue())))
    t.check("CSV quoting round-trips embedded quotes and commas",
            back[0]["full_file_path"] == '/x/say "hi", ok_prsv.wav')

    t.check("clean_path_input strips drag-and-drop quotes",
            clean_path_input('"/Volumes/CAR 066/x"') == "/Volumes/CAR 066/x")
    if os.name != "nt":
        t.check("clean_path_input undoes macOS Terminal escaping",
                clean_path_input(r"/Volumes/CAR\ 066/it\'s ") == "/Volumes/CAR 066/it's")

    # --- harvest() end to end against an emulated MediaInfo -------------------
    with tempfile.TemporaryDirectory(prefix="harvest_selftest_") as tmp:
        root = Path(tmp) / "in"
        layout = {
            # two partner sets in one run, at different depths
            ("ctsta", "ctsta_000001"): [
                "ctsta_000001_f00001_prsv.wav",
                "ctsta_000001_f00001_prsv.wav.md5",     # sidecar
                "ctsta_000001_f00001_access.VTT",       # sidecar, upper case
                "ctsta_000001_PBCore.Xml",              # sidecar, mixed case
            ],
            ("cbbb", "cbbb_000042"): [
                "cbbb_000099_f00001_prsv.VOB",          # filename disagrees with its folder
                "cbbb_000099_f00002_prsv.VOB",          # video-only segment -> flagged sequence
            ],
            ("2025-2026_DG_cddd", "cddd", "cddd_000007"): [
                "cddd_000007_f00001_access.HD.mp4",     # 2 video + 2 audio streams
                "cddd_000007_corrupt_prsv.wav",         # MediaInfo error
            ],
        }
        for folders, names_ in layout.items():
            folder = root.joinpath(*folders)
            folder.mkdir(parents=True)
            for n in names_:
                (folder / n).write_bytes(b"")
        (root / "notes.txt").write_text("not media\n", encoding="utf-8")

        wav = _spec(3000, audio=[{"Format": "PCM", "Channel(s)": "2", "SamplingRate": "96000",
                                  "BitDepth": "24", "BitRate_Mode": "CBR",
                                  "BitRate/String": "4 608 kb/s", "Duration/String3": "00:00:03.000"}],
                    overall="4 608 kb/s")
        vob1 = _spec(587500,
                     audio=[{"Format": "AC-3", "Channel(s)": "2", "SamplingRate": "48000", "BitDepth": "",
                             "Duration/String3": "00:09:47.500"}],
                     video=[{"Width": "720", "Height": "480", "Format": "MPEG Video",
                             "Duration/String3": "00:09:47.500"}])
        vob2 = _spec(33,
                     video=[{"Width": "720", "Height": "480", "Format": "MPEG Video",
                             "Duration/String3": "00:00:00.033"}])
        mp4 = _spec(587500,
                    audio=[{"Format": "AAC", "Channel(s)": "2", "SamplingRate": "48000", "BitDepth": "",
                            "Duration/String3": "00:09:47.500"},
                           {"Format": "AC-3", "Channel(s)": "6", "SamplingRate": "48000", "BitDepth": "",
                            "Duration/String3": "00:09:47.480"}],
                    video=[{"Width": "720", "Height": "480", "Format": "AVC",
                            "Duration/String3": "00:09:47.500"},
                           {"Width": "704", "Height": "576", "Format": "MPEG Video",
                            "Duration/String3": "00:09:47.480"}])
        specs = {
            "ctsta_000001_f00001_prsv.wav": wav,
            "cbbb_000099_f00001_prsv.VOB": vob1,
            "cbbb_000099_f00002_prsv.VOB": vob2,
            "cddd_000007_f00001_access.HD.mp4": mp4,
            "cddd_000007_corrupt_prsv.wav": "ERROR",
        }
        probed = []
        r = harvest(root, "mediainfo", DEFAULT_SEGMENT_PATTERN, show_progress=False,
                    _run=_emulated_mediainfo(specs, probed))
        by = {row["filename"]: row for row in r.rows}

        t.check("emulated harvest: expected rows",
                set(by) == {"ctsta_000001_f00001_prsv.wav", "cbbb_000099_f00001_prsv.VOB",
                            "cbbb_000099_f00002_prsv.VOB", "cddd_000007_f00001_access.HD.mp4"},
                f"got {sorted(by)}")
        t.check("two partner sets in one run: each file gets its own marc_code / object_identifier",
                {(k, v["marc_code"], v["object_identifier"]) for k, v in by.items()} == {
                    ("ctsta_000001_f00001_prsv.wav", "ctsta", "ctsta_000001"),
                    ("cbbb_000099_f00001_prsv.VOB", "cbbb", "cbbb_000042"),
                    ("cbbb_000099_f00002_prsv.VOB", "cbbb", "cbbb_000042"),
                    ("cddd_000007_f00001_access.HD.mp4", "cddd", "cddd_000007"),
                }, f"got {[(k, v['marc_code'], v['object_identifier']) for k, v in by.items()]}")
        t.check("filename that disagrees with its folder: object_identifier is the folder's, filename is the file's",
                by["cbbb_000099_f00001_prsv.VOB"]["object_identifier"] == "cbbb_000042"
                and by["cbbb_000099_f00001_prsv.VOB"]["filename"] == "cbbb_000099_f00001_prsv.VOB")
        t.check("full_file_path is the complete path to the file",
                all(Path(row["full_file_path"]).name == row["filename"]
                    and Path(row["full_file_path"]).is_file() for row in r.rows))
        t.check("sidecars (.md5, .VTT, .Xml) excluded and counted per extension",
                sidecar_counts(r) == {".md5": 1, ".vtt": 1, ".xml": 1}, f"got {sidecar_counts(r)}")
        t.check("sidecars never reach MediaInfo",
                not any(Path(n).suffix.lower() in ALWAYS_EXCLUDED_EXTENSIONS for n in probed),
                f"probed {sorted(set(probed))}")
        t.check("sidecar summary line format",
                sidecar_summary_line(r) == "Excluded sidecars: 1 .vtt, 1 .md5, 1 .xml", sidecar_summary_line(r))
        t.check("non-media file skipped and counted; unreadable file logged as an error",
                r.non_media == 1 and [Path(p).name for p, _ in r.errors] == ["cddd_000007_corrupt_prsv.wav"],
                f"non_media={r.non_media} errors={r.errors}")
        w = by["ctsta_000001_f00001_prsv.wav"]
        t.check("new audio columns: sampling rate and bit depth reported",
                (w["audio_channels"], w["audio_sampling_rate"], w["audio_bit_depth"]) == ("2", "96000", "24"),
                f"got {(w['audio_channels'], w['audio_sampling_rate'], w['audio_bit_depth'])}")
        t.check("bit depth is blank for a lossy codec",
                by["cddd_000007_f00001_access.HD.mp4"]["audio_bit_depth"] == "")
        m = by["cddd_000007_f00001_access.HD.mp4"]
        t.check("multi-stream (2 audio + 2 video): durations clean, stream 1 reported, count = 2",
                m["audio_duration"] == "00:09:47.500" and m["video_duration"] == "00:09:47.500"
                and m["width"] == "720" and m["audio_format"] == "AAC" and m["audio_stream_count"] == 2,
                f"got {m['audio_duration']!r} {m['video_duration']!r} {m['width']!r} {m['audio_format']!r}")
        t.check("video columns still populated (this tool is not Sound-only)",
                by["cbbb_000099_f00001_prsv.VOB"]["video_format"] == "MPEG Video"
                and by["cbbb_000099_f00001_prsv.VOB"]["width"] == "720")
        t.check("has_audio_stream / audio_stream_count for audio and video-only files",
                (by["cbbb_000099_f00001_prsv.VOB"]["has_audio_stream"], by["cbbb_000099_f00002_prsv.VOB"]["has_audio_stream"],
                 by["cbbb_000099_f00002_prsv.VOB"]["audio_stream_count"]) == ("yes", "no", 0))
        t.check("flagging unchanged: only the mixed audio / video-only sequence is flagged",
                set(r.flagged) == {"cbbb_000099"}, f"got {sorted(r.flagged)}")
        t.check("flagged or errors -> exit 1", exit_code_for(r) == EXIT_REVIEW)

        out = Path(tmp) / "out"
        out.mkdir()
        paths = write_outputs(r, {"input": root, "output": out / "h.csv"})
        with open(paths["csv"], newline="", encoding="utf-8") as fp:
            reader = csv.DictReader(fp)
            rows = list(reader)
        t.check("CSV header matches CSV_COLUMNS and has 4 rows",
                reader.fieldnames == CSV_COLUMNS and len(rows) == 4, f"got {reader.fieldnames}, {len(rows)} rows")
        flagged_text = paths["flagged"].read_text(encoding="utf-8")
        t.check("flagged_items.log keeps its v2.0.1 format",
                flagged_text.startswith("Flagged sequences -- mixed audio / video-only segments\n")
                and "=== cbbb_000099 (2 segments) ===" in flagged_text
                and "[f00002] cbbb_000099_f00002_prsv.VOB -> VIDEO-ONLY (silent), duration=00:00:00.033" in flagged_text
                and "[f00001] cbbb_000099_f00001_prsv.VOB -> HAS AUDIO" in flagged_text, flagged_text)
        t.check("harvest_errors.log lists the unreadable file",
                "cddd_000007_corrupt_prsv.wav -- MediaInfo General call failed"
                in paths["errors"].read_text(encoding="utf-8"))

        clean = harvest(root / "ctsta", "mediainfo", DEFAULT_SEGMENT_PATTERN, show_progress=False,
                        _run=_emulated_mediainfo(specs))
        t.check("clean run (no errors, nothing flagged, rows written) -> exit 0",
                exit_code_for(clean) == EXIT_OK and len(clean.rows) == 1, f"exit {exit_code_for(clean)}")
        empty = HarvestResult()
        t.check("zero rows -> exit 1", exit_code_for(empty) == EXIT_REVIEW)

    # --- main(): prompting and exit codes (no MediaInfo calls needed) ---------
    with tempfile.TemporaryDirectory(prefix="harvest_selftest_") as tmp:
        d = Path(tmp) / "side"
        d.mkdir()
        for n in ("a_prsv.wav.md5", "b_access.vtt", "c.xml", "d.VTT", "e.XML"):
            (d / n).write_text("x\n", encoding="utf-8")
        out = Path(tmp) / "o"
        out.mkdir()
        fake_exe = sys.executable   # any existing file: it is never run (all files are sidecars)

        code, so, se = _run_main(["--no-prompt", "-i", str(d), "-o", str(out / "s.csv"), "--mediainfo", fake_exe])
        t.check("only sidecars in the folder: exit 1 (zero rows), no MediaInfo call needed", code == EXIT_REVIEW, f"exit {code}\n{so}{se}")
        t.check("summary prints the per-extension sidecar counts",
                "Excluded sidecars: 2 .vtt, 1 .md5, 2 .xml" in so, so)

        code, so, se = _run_main(["--no-prompt", "-o", str(out / "u.csv"), "--mediainfo", fake_exe])
        t.check("--no-prompt without --input exits 2", code == EXIT_USAGE, f"exit {code}")
        code, so, se = _run_main(["--no-prompt", "-i", str(d), "--mediainfo", fake_exe])
        t.check("--no-prompt without --output exits 2", code == EXIT_USAGE, f"exit {code}")
        code, so, se = _run_main(["--no-prompt", "-i", str(Path(tmp) / "nope"), "-o", str(out / "u.csv"),
                                  "--mediainfo", fake_exe])
        t.check("missing input folder exits 2", code == EXIT_USAGE, f"exit {code}")
        code, so, se = _run_main(["-i", str(d), "-o", str(out / "u.csv"), "--segment-pattern", "(?P<prefix>x)",
                                  "--mediainfo", fake_exe])
        t.check("--segment-pattern without a 'segnum' group exits 2", code == EXIT_USAGE, f"exit {code}")
        code, so, se = _run_main(["-i", str(d), "-o", str(out / "u.csv"), "--mediainfo", str(Path(tmp) / "no-such-mediainfo")])
        t.check("MediaInfo not found exits 3", code == EXIT_NO_MEDIAINFO, f"exit {code}")

        # Missing inputs are prompted for, and bad answers re-prompt.
        code, so, se = _run_main(["--mediainfo", fake_exe],
                                 inputs=["", str(Path(tmp) / "nope"), str(d), str(out), str(out / "p.csv")])
        t.check("missing -i / -o are prompted for; blank, bad folder and a folder-as-output re-prompt",
                code == EXIT_REVIEW and (out / "p.csv").is_file()
                and so.count("A path is required.") == 1 and so.count("Input directory does not exist") == 1
                and so.count("Output must be a .csv file path") == 1, f"exit {code}\n{so}{se}")
        code, so, se = _run_main(["--mediainfo", fake_exe], inputs=[])
        t.check("no flags and no interactive input exits 2", code == EXIT_USAGE, f"exit {code}")


def _integration_tests(t, exe):
    print(f"\nIntegration tests (real MediaInfo: {exe})")
    with tempfile.TemporaryDirectory(prefix="harvest_selftest_") as tmp:
        d = Path(tmp) / "in"
        obj1 = d / "ctstmarc" / "ctstmarc_000001"
        obj2 = d / "ctstmarc" / "ctstmarc_000002"
        obj1.mkdir(parents=True)
        obj2.mkdir(parents=True)
        _write_wav(obj1 / "ctstmarc_000001_f01_prsv.wav", 3)                    # 16-bit, 8 kHz, mono
        _write_wav(obj1 / "ctstmarc_000001_f02_prsv.wav", 12)
        _write_wav(obj2 / "ctstmarc_000002_f01_access.HD.wav", 2, rate=48000, channels=2, sampwidth=3)  # 24-bit stereo
        _write_mkv(obj1 / "ctstmarc_000001_f03_prsv.mkv", 5, audio_tracks=2)   # two audio streams
        _write_mkv(obj1 / "ctstmarc_000001_f04_prsv.mkv", 4, audio_tracks=1, video=True)
        (obj1 / "ctstmarc_000001_f01_access.VTT").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
        (obj1 / "ctstmarc_000001_f01_prsv.wav.md5").write_text("d41d8cd98f00b204e9800998ecf8427e\n", encoding="utf-8")
        (obj1 / "ctstmarc_000001_PBCore.Xml").write_text("<pbcoreDescriptionDocument/>\n", encoding="utf-8")
        (d / "notes.txt").write_text("not media\n", encoding="utf-8")

        r = harvest(d, exe, DEFAULT_SEGMENT_PATTERN, show_progress=False)
        by = {row["filename"]: row for row in r.rows}
        t.check("expected media files in the rows (including the one with video)",
                set(by) == {"ctstmarc_000001_f01_prsv.wav", "ctstmarc_000001_f02_prsv.wav",
                            "ctstmarc_000001_f03_prsv.mkv", "ctstmarc_000001_f04_prsv.mkv",
                            "ctstmarc_000002_f01_access.HD.wav"}, f"got {sorted(by)}")
        t.check("no MediaInfo errors on test files", not r.errors, f"got {r.errors}")
        t.check("sidecars excluded before MediaInfo, counted per extension",
                sidecar_counts(r) == {".vtt": 1, ".md5": 1, ".xml": 1}, f"got {sidecar_counts(r)}")
        t.check("non-media file does not become a row", "notes.txt" not in by)

        w16 = by.get("ctstmarc_000001_f01_prsv.wav", {})
        w24 = by.get("ctstmarc_000002_f01_access.HD.wav", {})
        t.check("new columns on a real 16-bit / 8 kHz mono WAV",
                (w16.get("audio_sampling_rate"), w16.get("audio_bit_depth"), w16.get("audio_channels"))
                == ("8000", "16", "1"),
                f"got {(w16.get('audio_sampling_rate'), w16.get('audio_bit_depth'), w16.get('audio_channels'))}")
        t.check("new columns on a real 24-bit / 48 kHz stereo WAV",
                (w24.get("audio_sampling_rate"), w24.get("audio_bit_depth"), w24.get("audio_channels"))
                == ("48000", "24", "2"),
                f"got {(w24.get('audio_sampling_rate'), w24.get('audio_bit_depth'), w24.get('audio_channels'))}")
        t.check("rows carry marc_code / object_identifier / filename from their folders",
                (w16.get("marc_code"), w16.get("object_identifier"), w16.get("filename"))
                == ("ctstmarc", "ctstmarc_000001", "ctstmarc_000001_f01_prsv.wav")
                and (w24.get("object_identifier"), w24.get("filename"))
                == ("ctstmarc_000002", "ctstmarc_000002_f01_access.HD.wav"))
        t.check("full_file_path is the complete path to the file",
                all(Path(row["full_file_path"]).name == row["filename"]
                    and Path(row["full_file_path"]).is_file() for row in r.rows))
        m = by.get("ctstmarc_000001_f03_prsv.mkv")
        if m:
            t.check("multi-stream: audio_stream_count = 2", str(m["audio_stream_count"]) == "2",
                    f"got {m['audio_stream_count']!r}")
            t.check("multi-stream: audio_duration is a clean HH:MM:SS.mmm (last field of the category)",
                    bool(DUR_RE.match(m["audio_duration"])), f"got {m['audio_duration']!r}")
            t.check("multi-stream: single-stream fields not run together",
                    m["audio_channels"] == "1" and m["audio_sampling_rate"] == "8000",
                    f"channels={m['audio_channels']!r} rate={m['audio_sampling_rate']!r}")
        else:
            t.check("multi-stream file present in rows", False)
        t.check("every general_duration is a clean HH:MM:SS.mmm",
                all(DUR_RE.match(row["general_duration"]) for row in r.rows),
                f"got {[row['general_duration'] for row in r.rows]}")

        # Several partner sets in one run, at different depths, including a
        # file whose name does NOT start with its object folder's name.
        multi_root = Path(tmp) / "multi"
        layout = {
            ("2025-2026_DG_aaa", "caaa", "caaa_000001"): "caaa_000001_f01_prsv.wav",
            ("cbbb", "cbbb_000042"): "cbbb_000042_f01_access.wav",
            ("cccc", "cccc_000007"): "cccc_000099_f01_prsv.wav",   # name/folder mismatch
        }
        for folders, fname in layout.items():
            folder = multi_root.joinpath(*folders)
            folder.mkdir(parents=True)
            _write_wav(folder / fname, 1)
        r2 = harvest(multi_root, exe, DEFAULT_SEGMENT_PATTERN, show_progress=False)
        got = {(row["marc_code"], row["object_identifier"], row["filename"]) for row in r2.rows}
        want = {("caaa", "caaa_000001", "caaa_000001_f01_prsv.wav"),
                ("cbbb", "cbbb_000042", "cbbb_000042_f01_access.wav"),
                ("cccc", "cccc_000007", "cccc_000099_f01_prsv.wav")}
        t.check("multiple partner sets in one run: each file gets its own marc_code; "
                "object_identifier comes from the folder, not the filename", got == want, f"got {sorted(got)}")

        # End-to-end through the command line, as an unattended run would.
        out = Path(tmp) / "out"
        out.mkdir()
        me = str(Path(__file__).resolve())
        base = [sys.executable, me, "--mediainfo", exe, "--no-prompt"]
        p = subprocess.run(base + ["-i", str(d), "-o", str(out / "h.csv")],
                           capture_output=True, text=True, errors="replace")
        t.check("CLI run with nothing to review exits 0", p.returncode == EXIT_OK,
                f"exit {p.returncode}\n{p.stdout}{p.stderr}")
        t.check("CLI summary reports excluded sidecars per extension",
                "Excluded sidecars: 1 .vtt, 1 .md5, 1 .xml" in p.stdout, p.stdout)
        if (out / "h.csv").is_file():
            with open(out / "h.csv", newline="", encoding="utf-8") as fp:
                reader = csv.DictReader(fp)
                rows = list(reader)
            t.check("CSV header matches CSV_COLUMNS", reader.fieldnames == CSV_COLUMNS, f"got {reader.fieldnames}")
            t.check("CSV has 5 rows", len(rows) == 5, f"got {len(rows)}")
        else:
            t.check("CSV written", False)
        t.check("flagged_items.log and (no errors) no harvest_errors.log written next to the CSV",
                (out / FLAGGED_LOG).is_file() and not (out / ERRORS_LOG).exists(),
                f"found {sorted(p.name for p in out.iterdir())}")
        p = subprocess.run(base + ["-o", str(out / "h4.csv")], capture_output=True, text=True, errors="replace")
        t.check("--no-prompt without --input exits 2", p.returncode == EXIT_USAGE, f"exit {p.returncode}")


def run_selftest(mediainfo_arg=None):
    print(f"{TOOL_NAME} v{__version__} -- self-test\n")
    t = _SelfTest()
    _unit_tests(t)
    exe = find_mediainfo(mediainfo_arg)
    if exe:
        _integration_tests(t, exe)
    else:
        print()
        t.skip("integration tests", "MediaInfo CLI not found, so the new audio columns, multi-stream "
                                    "handling and exit codes against real MediaInfo were NOT verified")
    print(f"\n{t.passed} passed, {t.failed} failed, {t.skipped} skipped")
    if t.failed:
        print("SELF-TEST FAILED")
        return EXIT_SELFTEST_FAILED
    print("Self-test passed" + (" (with skips -- see above)" if t.skipped else ""))
    return EXIT_OK


# =============================================================================
# Entry point
# =============================================================================

def parse_args(argv):
    ap = argparse.ArgumentParser(
        description=f"{TOOL_NAME} -- recursive MediaInfo harvest to CSV. "
                    "Any input not given as a flag is prompted for interactively.",
        epilog="Exit codes: 0 ok, 1 finished but needs review, 2 usage/input error, "
               "3 MediaInfo not found, 4 self-test failed, 130 interrupted.",
    )
    ap.add_argument("-i", "--input", help="Input directory (searched recursively)")
    ap.add_argument("-o", "--output", help="Output CSV file; logs are written next to it")
    ap.add_argument("--segment-pattern", default=None,
                    help=("Regex with named groups 'prefix' and 'segnum' for grouping segmented "
                          r"items. Default matches <prefix>_f<N>_prsv.<ext>, e.g. "
                          "calauem_006976_f01_prsv.VOB"))
    ap.add_argument("--mediainfo", metavar="PATH", help="Path to the mediainfo CLI if it isn't on PATH")
    ap.add_argument("--no-prompt", action="store_true",
                    help="Never prompt: fail (exit 2) if -i/-o are missing")
    ap.add_argument("--selftest", action="store_true", help="Run the built-in self-test and exit")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap.parse_args(argv)


def main(argv=None):
    configure_stdio()
    args = parse_args(argv)
    if args.selftest:
        return run_selftest(args.mediainfo)

    mediainfo_exe = find_mediainfo(args.mediainfo)
    if not mediainfo_exe:
        print(MEDIAINFO_MISSING_MSG, file=sys.stderr)
        return EXIT_NO_MEDIAINFO

    try:
        s = resolve_settings(args)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_USAGE

    print(f"Reading files in:\n  {s['input']}\n")
    res = harvest(s["input"], mediainfo_exe, s["segment_pattern"])
    try:
        paths = write_outputs(res, s)
    except OSError as e:
        print(f"ERROR: could not write output: {e}", file=sys.stderr)
        return EXIT_USAGE
    print_summary(res, paths)
    return exit_code_for(res)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(EXIT_INTERRUPTED)
