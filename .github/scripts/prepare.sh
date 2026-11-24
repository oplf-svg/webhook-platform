#!/usr/bin/env bash
# Puts the two Python packages of the crash tests into build/venv, each checked against its fingerprint.
set -euo pipefail
cd "$(dirname "$0")/../.."

[ -x build/venv/bin/python ] || "${PYTHON:-python3}" -m venv build/venv
build/venv/bin/python -m pip install --quiet --disable-pip-version-check --require-hashes --only-binary :all: --requirement crash/requirements.txt
