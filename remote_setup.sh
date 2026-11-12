#!/usr/bin/env bash
# Run as the SSH user. Does not alter an existing shared /opt/cloak install.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m camoufox fetch
printf '%s\n' 'If Firefox libraries are missing, an administrator must run:' \
  "$(pwd)/.venv/bin/python -m playwright install-deps firefox" \
  'Run with PYTHON=.venv/bin/python ./run_parallel.sh'
