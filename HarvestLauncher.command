#!/bin/zsh
# -----------------------------------------------------------------------------
# MediaInfo Metadata Harvest Tool — Interactive Launcher
#
# A double-click-friendly front end for MediaInfoHarvest.zsh. Prompts the
# operator for an input directory and an output CSV path, validates both, then
# hands off to the core script. Both files must live in the same directory.
#
# Requires: zsh, mediainfo, and MediaInfoHarvest.zsh alongside this file.
# -----------------------------------------------------------------------------

# Run from this script's own folder so ./MediaInfoHarvest.zsh resolves.
cd "$(dirname "$0")"

echo "=== MediaInfo Metadata Harvest Tool ==="
echo

# ---- Input directory --------------------------------------------------------
read -r "?Enter input directory (full path): " INPUT_DIR
if [[ ! -d "$INPUT_DIR" ]]; then
  echo
  echo "ERROR: Input directory does not exist."
  echo "Press Return to exit."
  read
  exit 1
fi

# ---- Output file ------------------------------------------------------------
read -r "?Enter output CSV file (full path including .csv): " OUTPUT_FILE
OUTPUT_DIR=${OUTPUT_FILE:h}
if [[ ! -d "$OUTPUT_DIR" ]]; then
  echo
  echo "ERROR: Output directory does not exist."
  echo "Press Return to exit."
  read
  exit 1
fi

# ---- Run --------------------------------------------------------------------
echo
echo "Starting metadata harvest..."
echo

./MediaInfoHarvest.zsh -i "$INPUT_DIR" -o "$OUTPUT_FILE"

echo
echo "Done."
echo "Press Return to close."
read
