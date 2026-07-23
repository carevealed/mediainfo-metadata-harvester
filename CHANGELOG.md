# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
