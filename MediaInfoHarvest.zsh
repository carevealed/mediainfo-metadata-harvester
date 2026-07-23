#!/bin/zsh
# -----------------------------------------------------------------------------
# MediaInfo Metadata Harvest Tool
#
# Recursively scans an input directory for time-based media files and writes a
# CSV of selected technical metadata, read via the MediaInfo CLI. Files with no
# General duration are skipped, so still images and non-AV sidecar files are
# excluded automatically.
#
# Usage:    MediaInfoHarvest.zsh -i INPUT_DIRECTORY -o OUTPUT_FILE.csv
# Requires: zsh, mediainfo  (https://mediaarea.net/en/MediaInfo)
# -----------------------------------------------------------------------------

set -o errexit
set -o nounset

SCRIPT_NAME=$(basename "$0")

usage() {
  cat <<EOF
$SCRIPT_NAME — MediaInfo Metadata Harvest Tool

USAGE:
  $SCRIPT_NAME -i INPUT_DIRECTORY -o OUTPUT_FILE.csv

OPTIONS:
  -i  Input directory (required)
  -o  Output CSV file (required)
  -h  Show help

EXAMPLE:
  $SCRIPT_NAME -i /Volumes/Media -o ~/Desktop/output.csv
EOF
}

# ---- Parse options ----------------------------------------------------------
while getopts ":i:o:h" opt; do
  case $opt in
    i)  INPUT_DIR="$OPTARG" ;;
    o)  OUTPUT_FILE="$OPTARG" ;;
    h)  usage; exit 0 ;;
    \?) echo "ERROR: Invalid option -$OPTARG"; usage; exit 1 ;;
    :)  echo "ERROR: Option -$OPTARG requires an argument."; usage; exit 1 ;;
  esac
done

# ---- Validate input ---------------------------------------------------------
if [[ -z "${INPUT_DIR:-}" || -z "${OUTPUT_FILE:-}" ]]; then
  echo "ERROR: -i and -o are required."
  usage
  exit 1
fi

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "ERROR: Input directory does not exist:"
  echo "  $INPUT_DIR"
  exit 1
fi

OUTPUT_DIR=${OUTPUT_FILE:h}
if [[ ! -d "$OUTPUT_DIR" ]]; then
  echo "ERROR: Output directory does not exist:"
  echo "  $OUTPUT_DIR"
  exit 1
fi

# ---- Write CSV header -------------------------------------------------------
echo "filename,general_duration,video_duration,audio_duration,width,height,frame_rate,video_format,video_format_version,video_standard,video_color_space,video_bit_depth,video_scan_type,video_bit_rate_mode,video_bit_rate_value,video_maximum_bit_rate,aspect_ratio,audio_format,audio_format_info,audio_channels,audio_bit_rate_mode,audio_bit_rate_value,audio_muxing_mode,menu_format" > "$OUTPUT_FILE"

# Count files up front so the progress bar has a denominator.
TOTAL=$(find "$INPUT_DIR" -type f | wc -l | tr -d ' ')

echo "Reading files in:"
echo "  $INPUT_DIR"
echo

# ---- Progress bar helper ----------------------------------------------------
# Draws a fixed-width bar on the current line, e.g.  [########------------]  40% (102/256)
print_progress() {
  local current=$1 total=$2 width=20
  local pct=0 filled=0 i bar=""
  if [[ "$total" -gt 0 ]]; then
    pct=$(( current * 100 / total ))
    filled=$(( pct * width / 100 ))
  fi
  for (( i = 0; i < filled; i++ )); do bar+="#"; done
  for (( i = filled; i < width; i++ )); do bar+="-"; done
  printf '\r[%s] %3d%% (%d/%d)' "$bar" "$pct" "$current" "$total"
}

# ---- Process files ----------------------------------------------------------
count=0
find "$INPUT_DIR" -type f | while IFS= read -r f; do
  count=$(( count + 1 ))
  print_progress "$count" "$TOTAL"

  # Skip non-time-based files (no General duration -> images, sidecars, etc.)
  gdur_ms=$(mediainfo --Inform='General;%Duration%' "$f")
  if [[ -z "$gdur_ms" || "$gdur_ms" -eq 0 ]]; then
    continue
  fi

  # ---- Metadata fields ----
  gdur=$(mediainfo --Inform='General;%Duration/String3%' "$f")
  vdur=$(mediainfo --Inform='Video;%Duration/String3%' "$f")
  adur=$(mediainfo --Inform='Audio;%Duration/String3%' "$f")
  width=$(mediainfo --Inform='Video;%Width%' "$f")
  height=$(mediainfo --Inform='Video;%Height%' "$f")
  framerate=$(mediainfo --Inform='Video;%FrameRate/String%' "$f")
  vformat=$(mediainfo --Inform='Video;%Format%' "$f")
  vformatver=$(mediainfo --Inform='Video;%Format_Version%' "$f")
  vstandard=$(mediainfo --Inform='Video;%Standard%' "$f")
  vcolorspace=$(mediainfo --Inform='Video;%ColorSpace%' "$f")
  vbitdepth=$(mediainfo --Inform='Video;%BitDepth%' "$f")
  vscantype=$(mediainfo --Inform='Video;%ScanType%' "$f")
  vbrmode=$(mediainfo --Inform='Video;%BitRate_Mode%' "$f")
  vbrval=$(mediainfo --Inform='Video;%BitRate/String%' "$f")
  vmaxbr=$(mediainfo --Inform='Video;%BitRate_Maximum/String%' "$f")
  [[ -z "$vmaxbr" ]] && vmaxbr=$(mediainfo --Inform='General;%OverallBitRate/String%' "$f")
  aspect=$(mediainfo --Inform='Video;%DisplayAspectRatio/String%' "$f")
  aformat=$(mediainfo --Inform='Audio;%Format%' "$f")
  aformatinfo=$(mediainfo --Inform='Audio;%Format_AdditionalFeatures%' "$f")
  channels=$(mediainfo --Inform='Audio;%Channel(s)%' "$f")
  abrmode=$(mediainfo --Inform='Audio;%BitRate_Mode%' "$f")
  abrval=$(mediainfo --Inform='Audio;%BitRate/String%' "$f")
  amuxmode=$(mediainfo --Inform='Audio;%MuxingMode%' "$f")
  menuformat=$(mediainfo --Inform='Menu;%Format%' "$f")

  echo "\"$f\",\"$gdur\",\"$vdur\",\"$adur\",\"$width\",\"$height\",\"$framerate\",\"$vformat\",\"$vformatver\",\"$vstandard\",\"$vcolorspace\",\"$vbitdepth\",\"$vscantype\",\"$vbrmode\",\"$vbrval\",\"$vmaxbr\",\"$aspect\",\"$aformat\",\"$aformatinfo\",\"$channels\",\"$abrmode\",\"$abrval\",\"$amuxmode\",\"$menuformat\"" >> "$OUTPUT_FILE"
done

echo    # move off the progress-bar line
echo
echo "✅ Metadata harvest complete"
echo "Output saved to:"
echo "  $OUTPUT_FILE"
