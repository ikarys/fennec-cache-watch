"""Prefix-cache health of fennec sessions, read from its SQLite log (read-only).
Each call should reuse the whole previous prompt of its session; MISS = more than
--threshold tokens re-prefilled with no compaction/rewind in between; others = calls
of other fennec sessions in between. probe.py tests the server itself.
Usage: cache_watch.py [--server-log ~/workspace/hominfer/logs/swift15.log] [--last N] [--since H] [-v [-a]] [--session PREFIX]"""
import argparse, bisect, json, os, re, sqlite3, datetime
BREAKS = ('context.compacted', 'session.rewound', 'turn.snapshot')

LOG_LINE = re.compile(r"^(\S+ \S+) +INFO +req#(\d+) (started|done) \| openai-chat (non-stream|stream)?")

def external_requests(path):
    """[start_ms, end_ms] of the server's non-streamed requests: fennec always streams,
    so these are other clients (scripts, delegations) sharing the server's cache."""
    spans = {}
    for line in open(path, errors='replace'):
        m = LOG_LINE.match(line)
        if not m: continue
        ts = datetime.datetime.strptime(m[1], '%Y-%m-%d %H:%M:%S.%f').timestamp() * 1000
        if m[3] == 'started':
            if m[4] == 'non-stream': spans[m[2]] = [ts, float('inf')]
        elif m[2] in spans: spans[m[2]][1] = ts
    return list(spans.values())

def db_path():
    return os.environ.get('FENNEC_DB', os.path.expanduser('~/.local/share/fennec/fennec.db'))

def parse_call(seq, ts, payload):
    try: stats = json.loads(payload).get('data', {}).get('stats')
    except Exception: return None
    if not isinstance(stats, dict): return None
    return {'seq': seq, 'ts': ts, 'prefill': int(stats.get('prefillTokens') or 0),
            'cached': int(stats.get('cachedPromptTokens') or 0)}

def load_calls(conn, since_h):
    q = "SELECT session_id, seq, timestamp, payload FROM events WHERE event_type='message.done'"
    p = ()
    if since_h is not None:
        cutoff = int((datetime.datetime.now().timestamp() - since_h * 3600) * 1000)
        q += " AND timestamp >= ?"; p = (cutoff,)
    calls = {}
    for sid, seq, ts, payload in conn.execute(q, p):
        call = parse_call(seq, ts, payload)
        if call: calls.setdefault(sid, []).append(call)
    for v in calls.values(): v.sort(key=lambda c: c['seq'])
    return calls

def select_sessions(calls, prefix, limit):
    sids = [s for s in calls if prefix is None or s.startswith(prefix)]
    if not sids: return []
    sids.sort(key=lambda s: max(c['ts'] for c in calls[s]), reverse=True)
    return sids if limit is None else sids[:limit]

def load_session_info(conn, sids):
    info = {}
    if sids:
        q = f"SELECT id, title, parent_id FROM sessions WHERE id IN ({','.join('?' * len(sids))})"
        for sid, title, parent in conn.execute(q, sids): info[sid] = (title or '', bool(parent))
    return info

def analyze_session(conn, sid, calls, threshold, all_ts, ext):
    ph = ','.join('?' * len(BREAKS))
    breaks = list(conn.execute(f"SELECT seq, event_type FROM events WHERE session_id=? AND event_type IN ({ph}) ORDER BY seq", (sid,) + BREAKS))
    st = {'calls': len(calls), 'prefill': 0, 'cached': 0, 'misses': 0, 'breaks': 0, 'wasted': 0, 'details': []}
    prev = None
    for call in calls:
        st['prefill'] += call['prefill']; st['cached'] += call['cached']
        if prev is None:
            status, lost, prev_prefill, break_types, others, ext_n = 'first', 0, 0, [], 0, 0
        else:
            expected = prev['prefill']; lost = max(0, expected - call['cached'])
            break_types = [t for s, t in breaks if prev['seq'] < s < call['seq']]
            if lost > threshold: status = 'BREAK' if break_types else 'MISS'
            else: status = 'ok'
            prev_prefill = expected
            ext_n = sum(1 for a, b in ext if a < call['ts'] and b > prev['ts'])
            # calls of other sessions in between: they may have evicted this session's cache
            others = bisect.bisect_left(all_ts, call['ts']) - bisect.bisect_right(all_ts, prev['ts'])
        if status == 'MISS': st['misses'] += 1; st['wasted'] += lost
        if status == 'BREAK': st['breaks'] += 1
        st['details'].append({**call, 'status': status, 'lost': lost, 'prev_prefill': prev_prefill, 'breaks': break_types, 'others': others, 'ext': ext_n})
        prev = call
    return st

def print_details(details, all_calls):
    for d in details:
        if not all_calls and d['status'] not in ('MISS', 'BREAK'): continue
        t = datetime.datetime.fromtimestamp(d['ts'] / 1000).strftime('%H:%M:%S')
        print(f"  {d['seq']:>5} {t} prev={d['prev_prefill']:>7} prefill={d['prefill']:>7} "
              f"cached={d['cached']:>7} lost={d['lost']:>7} others={d['others']:>3} ext={d['ext']:>2} {' '.join(d['breaks'])}")

def line(label, title, child, calls, prefill, cached, misses, breaks, wasted):
    hit = cached / prefill * 100 if prefill else 0.0
    return (f"{label:<24} {title:<30} {'↳' if child else ' ':>2} {calls:>6} "
            f"{prefill / 1000:>9.1f} {hit:>5.1f}% {misses:>7} {breaks:>7} {wasted / 1000:>9.1f}")

def main():
    ap = argparse.ArgumentParser(description='Watch LLM prompt prefix cache health.')
    ap.add_argument('--last', type=int); ap.add_argument('--session', metavar='PREFIX')
    ap.add_argument('--since', type=float, metavar='HOURS'); ap.add_argument('-v', action='store_true')
    ap.add_argument('-a', action='store_true'); ap.add_argument('--threshold', type=int, default=512)
    ap.add_argument('--server-log', metavar='PATH', help='ninfer log: count non-fennec requests overlapping each miss (ext=)')
    args = ap.parse_args()
    verbose = args.v or args.a or bool(args.session)
    try:
        conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
        conn.execute('SELECT 1')
    except sqlite3.OperationalError as e:
        print(f"cannot open database: {e}"); raise SystemExit(1)
    calls = load_calls(conn, args.since)
    limit = args.last if args.last is not None else (None if args.session is not None else 10)
    sids = select_sessions(calls, args.session, limit)
    info = load_session_info(conn, sids)
    ext = external_requests(args.server_log) if args.server_log else []
    all_ts = sorted(c['ts'] for v in calls.values() for c in v)
    print(f"{'session':<24} {'title':<30} {'':>2} {'calls':>6} {'prefill_k':>9} {'hit%':>6} {'misses':>7} {'breaks':>7} {'wasted_k':>9}")
    total = [0, 0, 0, 0, 0, 0]; any_miss = False
    for sid in sids:
        st = analyze_session(conn, sid, calls[sid], args.threshold, all_ts, ext)
        title, child = info.get(sid, ('', False))
        print(line(sid[:24], title[:30], child, st['calls'], st['prefill'], st['cached'], st['misses'], st['breaks'], st['wasted']))
        if verbose: print_details(st['details'], args.a)
        any_miss = any_miss or st['misses'] > 0
        for i, key in enumerate(('calls', 'prefill', 'cached', 'misses', 'breaks', 'wasted')): total[i] += st[key]
    print(line('TOTAL', '', False, *total))
    raise SystemExit(1 if any_miss else 0)

if __name__ == '__main__': main()
