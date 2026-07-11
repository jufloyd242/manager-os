#!/usr/bin/env bash
# Start Manager OS — macOS double-click launcher
#
# Resolves its own directory, changes to the repository root,
# and runs ./manager-os start.
#
# Double-click this file in Finder to start Manager OS.
# Terminal output is preserved so you can see startup status.

cd "$(dirname "${BASH_SOURCE[0]}")"
./manager-os start
echo ""
echo "Press Enter to close this window."
read -r