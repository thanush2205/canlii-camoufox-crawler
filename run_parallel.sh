#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
: "${DATAIMPULSE_USER:?DATAIMPULSE_USER not set}"
: "${DATAIMPULSE_PASS:?DATAIMPULSE_PASS not set}"
export NWORKERS="${NWORKERS:-2}" GOAL="${GOAL:-50}" OUT="${OUT:-canlii_data}"
PYTHON="${PYTHON:-/opt/cloak/venv/bin/python}"
[[ "$NWORKERS" =~ ^[1-9][0-9]*$ && "$GOAL" =~ ^[1-9][0-9]*$ ]] || exit 2
mkdir -p "$OUT"
exec 9>"$OUT/run.lock"
flock -n 9 || { echo 'This output directory already has an active run'; exit 2; }
if [[ ! -s "$OUT/urls.txt" && -f seed_urls.txt ]]; then
  cp seed_urls.txt "$OUT/urls.txt"
fi
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup INT TERM EXIT
for ((i=1; i<=NWORKERS; i++)); do
  WORKER="$i" HEADLESS=true "$PYTHON" -u canlii_crawler.py >> "$OUT/worker-$i.log" 2>&1 9>&- &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
pids=()
"$PYTHON" summarize_crawl.py --out "$OUT" --goal "$GOAL" | tee "$OUT/summary.log"
