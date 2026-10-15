#!/bin/bash
# Double-click launcher (macOS). Thin wrapper around the cross-platform
# mediainfo_harvest.py -- runs it with no arguments, which drops it into
# interactive prompt mode. Any arguments given are passed straight through.
cd "$(dirname "$0")" || exit 1

pause_before_close() {
    echo
    read -r -p "Press Enter to close this window..." _
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found."
    echo "Install Python 3 from https://www.python.org/downloads/macos/"
    echo "(or via Homebrew: brew install python3)"
    pause_before_close
    exit 1
fi

python3 mediainfo_harvest.py "$@"
status=$?
pause_before_close
exit $status
