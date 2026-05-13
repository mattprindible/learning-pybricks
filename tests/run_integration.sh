#!/usr/bin/env bash
# Integration test runner — requires server.py running, hub + iPhone connected.
#
# Usage: ./tests/run_integration.sh
#
# Runs seam contracts first (fast sanity check), then multi-client scenarios.
# Stops on first suite failure.

set -e
cd "$(dirname "$0")/.."

echo "=== seam_check (contract tests) ==="
uv run python tests/seam_check.py

echo ""
echo "=== test_hardware_multi (multi-client + real hardware) ==="
uv run python tests/test_hardware_multi.py
