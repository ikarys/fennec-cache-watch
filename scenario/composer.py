"""Composer actions vs the prompt cache: sends one message after each action
(mode, effort, MCP toggle) in one session on a sandbox server (sandbox.sh), so
cache_watch.py or cache-hunter can tell which action breaks the cached prefix.
Usage: python3 -I composer.py [port]"""
import json, sqlite3, sys, time, urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else '7790'
BASE = f'http://127.0.0.1:{PORT}'
ROOT = '/tmp/fennec-cache-sandbox'
STEPS = [
    ('baseline', None, None),
    ('mode plan', 'mode', {'mode': 'plan'}),
    ('mode ask', 'mode', {'mode': 'ask'}),
    ('effort low', 'effort', {'effort': 'low'}),
    ('effort xhigh', 'effort', {'effort': 'xhigh'}),
    ('mcp mem0 on', 'mcp', {'disabled': ['notion']}),
    ('mcp mem0 off', 'mcp', {'disabled': ['notion', 'mem0']}),
    ('mode builder', 'mode', {'mode': 'builder'}),
]


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), method='POST', headers={
        'Origin': BASE, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b'{}')


def turns_done(session):
    db = sqlite3.connect(f'file:{ROOT}/fennec.db?mode=ro', uri=True)
    try:
        return db.execute("select count(*) from events where session_id = ? and event_type in ('chat.done', 'chat.error')",
                          (session,)).fetchone()[0]
    finally:
        db.close()


def wait(session, count, timeout=900):
    end = time.time() + timeout
    while turns_done(session) < count:
        if time.time() > end:
            sys.exit(f'timeout waiting for turn {count}')
        time.sleep(2)


project = open(f'{ROOT}/project').read().strip()
session = post('/api/sessions', {'projectId': project, 'mode': 'ask',
                                 'prompt': 'Read calc.py and say in one line what it does.'})['id']
print('session', session, flush=True)
for done, (label, action, body) in enumerate(STEPS, 1):
    if action:
        post(f'/api/sessions/{session}/{action}', body)
        post(f'/api/sessions/{session}/messages', {'content': f'After "{label}": answer in one word, ok?'})
    wait(session, done)
    print(f'{done}. {label}', flush=True)
