#!/bin/sh
# Run the fennec perf harness (real model) through the cache-hunter proxy.
set -u
# FENNEC_DIR: the fennec checkout to measure; CACHE_HUNTER_DIR: a cache-hunter checkout.
HERE=$(cd "$(dirname "$0")" && pwd)
FENNEC_DIR=${FENNEC_DIR:-$HOME/workspace/fennec}
CACHE_HUNTER_DIR=${CACHE_HUNTER_DIR:-$HOME/workspace/cache-hunter}
OUT=${OUT:-$HERE/out}
mkdir -p "$OUT"

(cd "$CACHE_HUNTER_DIR" && exec npx tsx src/index.ts) > "$OUT/perf-ch.log" 2>&1 &
CH_PID=$!

ok=""
for i in $(seq 1 60); do
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8787/v1/models; then
    ok=1
    break
  fi
  sleep 0.5
done
if [ -z "$ok" ]; then
  echo "cache-hunter proxy did not come up:" >&2
  tail -20 "$OUT/perf-ch.log" >&2
  kill "$CH_PID" 2>/dev/null
  exit 1
fi
echo "cache-hunter proxy up on :8787"
curl -s -X POST http://127.0.0.1:4000/api/capture/start > /dev/null || { echo "capture start failed" >&2; exit 1; }
echo "capture started"

: > "$OUT/perf-tap.jsonl"
TAP_UPSTREAM=http://127.0.0.1:8787/v1 TAP_PORT=8899 TAP_OUT="$OUT/perf-tap.jsonl" python3 "$HERE/perf-tap.py" > "$OUT/perf-tap.log" 2>&1 &
TAP_PID=$!
for i in $(seq 1 40); do
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8899/v1/models; then break; fi
  sleep 0.25
done
echo "usage tap up on :8899"

FENNEC_LLM_URL=http://127.0.0.1:8899/v1 python3 "$FENNEC_DIR/scripts/perf/run.py" --mode real
rc=$?

curl -s -X POST http://127.0.0.1:4000/api/capture/stop > /dev/null
kill "$TAP_PID" "$CH_PID" 2>/dev/null
wait "$TAP_PID" "$CH_PID" 2>/dev/null
exit "$rc"
