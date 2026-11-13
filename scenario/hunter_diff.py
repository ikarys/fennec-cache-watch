"""Request-to-request diff of the latest cache-hunter capture: which parameters changed and
whether the messages kept the previous prefix. A changed param with an intact prefix and a
cache miss means the break is in the server or its chat template, not in fennec."""
import sqlite3, json, sys, glob, os
p = max([f for f in glob.glob(os.path.expanduser('~/workspace/cache-hunter-src/data/*.db')) if os.path.getsize(f)], key=os.path.getmtime)
d = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
rows = [json.loads(b) for (b,) in d.execute("select body from requests where path like '%chat/completions' order by timestamp")]
print(p, len(rows))
def ser(r):  # the bytes that matter: everything except messages, then messages
    return json.dumps({k: v for k, v in r.items() if k != 'messages'}, sort_keys=True), r['messages']
for i in range(1, len(rows)):
    a, b = rows[i-1], rows[i]
    keys = sorted(k for k in set(a) | set(b) if k != 'messages' and a.get(k) != b.get(k))
    ma, mb = a['messages'], b['messages']
    n = next((j for j in range(min(len(ma), len(mb))) if ma[j] != mb[j]), min(len(ma), len(mb)))
    print(f'{i:2} params changed: {[(k, a.get(k) if k!="tools" else len(a.get(k) or []), b.get(k) if k!="tools" else len(b.get(k) or [])) for k in keys]}  messages {len(ma)}->{len(mb)} first diff at {n}' + ('' if n >= len(ma) else ' (PREFIX BROKEN)'))
