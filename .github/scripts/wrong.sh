#!/usr/bin/env bash
# Proves that the tests can fail: one mistake at a time, see crash/wrong.py. "2/4" is the second of four parts.
set -euo pipefail
cd "$(dirname "$0")/../.."

bash .github/scripts/prepare.sh
cd crash
../build/venv/bin/python wrong.py "$@"
