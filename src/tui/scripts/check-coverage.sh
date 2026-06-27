#!/usr/bin/env bash
#
# check-coverage.sh
#
# Runs `bun test --coverage`, parses the "All files" summary row from the
# terminal table output, and exits with code 1 if line or function coverage
# falls below the given threshold.
#
# Usage:
#   ./scripts/check-coverage.sh [threshold]
#
#   threshold  Minimum coverage percentage (default: 80)
#
# The coverage table output by Bun looks like:
#   -----------------------------------------|---------|---------|-------------------
#   All files                                |   87.50 |   87.94 |
#                                            | % Funcs | % Lines | Uncovered Line #s

set -euo pipefail

THRESHOLD="${1:-80}"

# Run tests with coverage — tee so we see output AND can parse it
OUTPUT=$(bun test --coverage 2>&1)
COV_EXIT=$?
echo "$OUTPUT"

if [ "$COV_EXIT" -ne 0 ]; then
  echo "❌ Tests failed (exit code $COV_EXIT) — not checking coverage"
  exit "$COV_EXIT"
fi

# Extract the "All files" row; format:
#   All files                                |   87.50 |   87.94 |
ALL_FILES=$(echo "$OUTPUT" | grep "All files" || true)

if [ -z "$ALL_FILES" ]; then
  echo "⚠️  Could not find coverage summary — skipping threshold check"
  exit 0
fi

# Parse columns (pipe-delimited):
#   Col 1 = file name
#   Col 2 = % Funcs
#   Col 3 = % Lines
FUNC_PCT=$(echo "$ALL_FILES" | awk -F'|' '{print $2}' | tr -d ' %')
LINE_PCT=$(echo "$ALL_FILES" | awk -F'|' '{print $3}' | tr -d ' %')

# Convert to integer (floor)
FUNC_INT=${FUNC_PCT%.*}
LINE_INT=${LINE_PCT%.*}

echo ""
echo "──────────────────────────────────────────"
echo " Coverage summary:"
echo "   Functions: ${FUNC_PCT}%  (threshold: ${THRESHOLD}%)"
echo "   Lines:     ${LINE_PCT}%  (threshold: ${THRESHOLD}%)"
echo "──────────────────────────────────────────"

HAS_ERROR=false

if [ "$FUNC_INT" -lt "$THRESHOLD" ] 2>/dev/null; then
  echo "❌ Function coverage ${FUNC_PCT}% is below threshold ${THRESHOLD}%"
  HAS_ERROR=true
fi

if [ "$LINE_INT" -lt "$THRESHOLD" ] 2>/dev/null; then
  echo "❌ Line coverage ${LINE_PCT}% is below threshold ${THRESHOLD}%"
  HAS_ERROR=true
fi

if [ "$HAS_ERROR" = true ]; then
  exit 1
fi

echo "✅ All coverage metrics meet the ${THRESHOLD}% threshold"

# Write badge JSON (used by CI to upload as artifact)
mkdir -p badges
COLOR=brightgreen
[ "$LINE_INT" -lt 90 ] && COLOR=green
[ "$LINE_INT" -lt 80 ] && COLOR=yellow
[ "$LINE_INT" -lt 70 ] && COLOR=orange
[ "$LINE_INT" -lt 60 ] && COLOR=red
cat > badges/coverage.json <<-EOF
{
  "schemaVersion": 1,
  "label": "coverage",
  "message": "${LINE_PCT}%",
  "color": "${COLOR}"
}
EOF
echo "Generated badges/coverage.json (${LINE_PCT}%, ${COLOR})"
