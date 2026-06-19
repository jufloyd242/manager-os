#!/usr/bin/env bash
# No-cost validation script — runs all checks that don't require
# external services (Gemini, Google Workspace, OpenAI, etc.)
#
# Safe to run locally. No API calls. No live data fetches.

set -euo pipefail

echo "=== No-Cost Validation ==="
echo ""

# 1. Run all tests
echo ">>> Running pytest..."
pytest tests/ -q
echo ""

# 2. Compile check all Python source
echo ">>> Compiling src/manager_os..."
python -m compileall src/manager_os -q
echo ""

# 3. CLI dry-run commands (safe, no external calls)
echo ">>> Running CLI dry-run commands..."
manager-os daily --dry-run --skip-project-index --no-workspace || true
manager-os feedback-summary || true
manager-os project-memory-report || true
echo ""

echo "=== Validation complete ==="
