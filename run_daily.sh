#!/bin/bash
# Daily runner for the Playwright/pytest suite.
# Called by the launchd job com.eq.automation.daily.

set -euo pipefail

PROJECT_DIR="/Users/himanshucosx/Documents/GitHub/Eq_automation"
cd "$PROJECT_DIR"

# Activate the project's virtualenv
source venv/bin/activate

# Make sure Playwright browsers are available
python -m playwright install chromium >/dev/null 2>&1 || true

# Timestamp for this run's log
TS="$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p reports/daily-logs

# Run the suite; capture everything to a dated log file
pytest > "reports/daily-logs/run_${TS}.log" 2>&1
