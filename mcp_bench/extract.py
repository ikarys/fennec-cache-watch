"""Extracts the bench inputs from fennec's store, read-only: base.json (the
frozen system prompt and fennec's own tools) and mcp_tools.json (the schemas of
the given MCP servers, as fennec sent them). They hold private prompts and
schemas, so they are never committed.
Usage: python3 -I extract.py [server ...]   (default: mem0 notion; FENNEC_DB overrides the store)"""
import json, os, sqlite3, sys

servers = sys.argv[1:] or ['mem0', 'notion']
prefixes = tuple(f'{s}_' for s in servers)
here = os.path.dirname(os.path.abspath(__file__))
path = os.environ.get('FENNEC_DB', os.path.expanduser('~/.local/share/fennec/fennec.db'))
db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
snapshots = [json.loads(p)['data'] for (p,) in db.execute(
    "select payload from events where event_type = 'turn.snapshot' order by id desc limit 400")]
mcp, base = {}, None
for snap in snapshots:
    tools = snap.get('cachedTools') or []
    for tool in tools:
        name = tool['function']['name']
        if name.startswith(prefixes):
            mcp.setdefault(name, tool)
    if base is None and any(t['function']['name'].startswith(prefixes) for t in tools):
        base = {'system': snap['cachedSystemPrompt'],
                'tools': [t for t in tools if not t['function']['name'].startswith(prefixes)]}
if base is None:
    sys.exit(f'no snapshot with {servers} enabled in the last 400 turns: enable them in a session first')
json.dump(base, open(os.path.join(here, 'base.json'), 'w'))
json.dump({s: [t for n, t in mcp.items() if n.startswith(f'{s}_')] for s in servers},
          open(os.path.join(here, 'mcp_tools.json'), 'w'))
print(f"base: {len(base['tools'])} tools; mcp: " + ', '.join(
    f"{s} {sum(n.startswith(f'{s}_') for n in mcp)}" for s in servers))
