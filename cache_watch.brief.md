Write a single Python 3 script `cache_watch.py`, stdlib only (sqlite3, argparse, json, os, datetime). Output ONLY the script in one ```python block.

Purpose: check whether a local LLM server's prompt prefix cache stays effective across the model calls of an agent session, by reading the agent's SQLite event log. The server reuses its KV cache only for an exact prefix of an earlier request, so within one session each call's prompt should start with the whole previous call's prompt.

Database: path from env FENNEC_DB, default ~/.local/share/fennec/fennec.db. Open READ-ONLY: sqlite3.connect(f"file:{path}?mode=ro", uri=True).
Tables:
  sessions(id TEXT, project_id, workdir, title TEXT, created_at TEXT, updated_at TEXT, parent_id TEXT)
  events(id INTEGER, session_id TEXT, seq INTEGER, timestamp INTEGER /*unix ms*/, event_type TEXT, payload TEXT /*JSON*/)
A model call = event_type 'message.done' whose payload JSON has data.stats, e.g.
  {"type":"message.done","data":{"messageId":"…","stats":{"model":"qwen3.8-27b","totalTime":2.99,"prefillTokens":51728,"cachedPromptTokens":51382,"generationTokens":486,"prefillSpeed":859.4,"generationSpeed":187.5}}}
(prefillTokens = whole prompt incl. cached part; cachedPromptTokens = part served from cache). Skip message.done without stats.
"Expected break" events (history legitimately rewritten): event_type in ('context.compacted','session.rewound','turn.snapshot').

Algorithm, per session, calls ordered by seq:
- First call: report as "first" (cached/prefill shows the cross-session shared prefix), not a miss.
- Call i>0: expected = prev.prefillTokens; lost = max(0, expected - cached). breaks = types of expected-break events with prev.seq < seq < this.seq.
  - if lost > THRESHOLD (arg --threshold, default 512): status "BREAK" if breaks else "MISS".
  - else "ok".
- Per session: calls, sum prefill, sum cached, hit% = cached/prefill, misses, breaks, wasted = sum(lost) over MISS calls (tokens re-prefilled for no reason).

CLI:
  cache_watch.py                 last 10 sessions having calls (by latest call timestamp), one summary line each, then a TOTAL line
  --last N                       number of sessions
  --session PREFIX               only sessions whose id starts with PREFIX (implies -v)
  --since HOURS                  only calls with timestamp within the last HOURS
  -v                             also print every MISS/BREAK call: seq, local time HH:MM:SS, prev prefill, prefill, cached, lost, break types
  -a                             with -v, print all calls, not only MISS/BREAK
  --threshold T
Summary line columns (aligned, fixed width): session id (first 24 chars), title (truncated 30, '' if null), child marker '↳' if parent_id, calls, prefill (k tokens, 1 decimal), hit%, misses, breaks, wasted (k).
Exit code 1 if any MISS in the selection, else 0 (so it can be used in a check).
Keep it short and readable (~120 lines max), functions with clear names, no classes needed.
