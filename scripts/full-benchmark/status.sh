#!/bin/bash
# Show how far the full benchmark has got. Changes nothing.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/settings.sh"

count() { ls "$PACKS/$1/$2"/*/manifest.json 2>/dev/null | wc -l | tr -d ' '; }
turns() { cat "$PACKS/$1/$2"/*/turns.jsonl 2>/dev/null | wc -l | tr -d ' '; }

echo "Packs complete (turn lines logged):"
printf '  %-52s %3s of 10   (%s turn lines)\n' "$LOCOMO_DEFAULTS" "$(count "$LOCOMO_DEFAULTS" locomo)" "$(turns "$LOCOMO_DEFAULTS" locomo)"
printf '  %-52s %3s of 10   (%s turn lines)\n' "$LOCOMO_PER_TURN" "$(count "$LOCOMO_PER_TURN" locomo)" "$(turns "$LOCOMO_PER_TURN" locomo)"
printf '  %-52s %3s of 500  (%s turn lines)\n' "$LME_DEFAULTS" "$(count "$LME_DEFAULTS" longmemeval)" "$(turns "$LME_DEFAULTS" longmemeval)"
printf '  %-52s %3s of 500  (%s turn lines)\n' "$LME_PER_TURN" "$(count "$LME_PER_TURN" longmemeval)" "$(turns "$LME_PER_TURN" longmemeval)"
echo
if pgrep -f "full-benchmark/run-builds.sh" >/dev/null; then echo "Driver: running"; else echo "Driver: not running"; fi
if pgrep -f "extracted_packs build" >/dev/null; then echo "Build process: running"; fi
if pgrep -f "product_packing gate" >/dev/null; then echo "Gate process: running"; fi
echo
echo "Last driver log lines ($RESULTS_DIR/driver.log):"
tail -8 "$RESULTS_DIR/driver.log" 2>/dev/null || echo "  (no log yet)"
