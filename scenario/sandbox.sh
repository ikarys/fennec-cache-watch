#!/usr/bin/env bash
# Starts a throwaway fennec server for cache scenarios: an empty copy of the
# real store (settings and MCP servers kept, no project, session or queue, so
# nothing resumes and no branch lands in a real repository) and a scratch repo.
# Usage: sandbox.sh <fennec binary> [port] [llm url]
set -euo pipefail
BIN=${1:?fennec binary}; PORT=${2:-7790}; LLM=${3:-}
ROOT=/tmp/fennec-cache-sandbox; WORK=/tmp/fennec-cache-work
rm -rf "$ROOT" "$WORK"; mkdir -p "$ROOT" "$WORK"
cp ~/.local/share/fennec/secret.key "$ROOT/"
python3 -I - "$ROOT/fennec.db" "$LLM" <<'PY'
import json, os, sqlite3, sys
src = sqlite3.connect('file:' + os.path.expanduser('~/.local/share/fennec/fennec.db') + '?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[1]); src.backup(dst)
for table in ('events', 'project_events', 'sessions', 'projects'):
    dst.execute(f'delete from {table}')
if sys.argv[2]:  # Settings' local endpoint wins over FENNEC_LLM_URL: point it at the given URL
    (raw,) = dst.execute("select value from settings where key = 'preferences'").fetchone()
    prefs = json.loads(raw)
    for provider in prefs.get('providers', []):
        if provider.get('id') == 'local':
            provider['baseUrl'] = sys.argv[2]
    dst.execute("update settings set value = ? where key = 'preferences'", (json.dumps(prefs),))
dst.commit(); dst.execute('vacuum')
PY
cd "$WORK" && git init -q && echo "# scratch" > README.md && printf 'def add(a, b):\n    return a + b\n' > calc.py
git add -A && git -c user.email=t@t -c user.name=t commit -qm init
cd "$ROOT"
env FENNEC_DB="$ROOT/fennec.db" ${LLM:+FENNEC_LLM_URL=$LLM} nohup "$BIN" serve --port "$PORT" > server.log 2>&1 &
echo $! > pid
sleep 3
curl -s -H "Origin: http://127.0.0.1:$PORT" -H "Content-Type: application/json" -X POST "http://127.0.0.1:$PORT/api/projects" -d "{\"workdir\":\"$WORK\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])' > project
echo "sandbox on :$PORT (pid $(cat pid)), project $(cat project); stop with: kill \$(cat $ROOT/pid)"
