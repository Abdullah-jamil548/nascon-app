#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export DAY1_SALT="${DAY1_SALT:-demo-salt}"
export PYTHONPATH="${ROOT}/backend"
python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:coverage-raw.json
python tools/write_coverage.py
