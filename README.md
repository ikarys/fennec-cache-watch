# fennec-cache-watch

Test tooling for [fennec](https://github.com/ikarys/fennec)'s prompt cache, kept out of the fennec
repository. fennec freezes its prompt so a local inference server can reuse its KV cache for the
exact prefix; these scripts check that it does, and measure what breaks it.

| Tool | What it answers |
|---|---|
| `cache_watch.py` | Per session, which model calls re-prefilled more than the previous prompt (read-only on the store). |
| `probe.py` | Does the server itself keep a prefix cached across turns, unrelated requests, idle time? |
| `scenario/` | Which composer action (mode, effort, MCP toggle) breaks the cache, on a throwaway fennec. |
| `mcp_bench/` | Does a model call MCP tools as well when they are lazy-loaded (`mcp_tools` / `mcp_call`) as when declared natively, and at what prompt cost? |

`scenario/` and `just composer` run behind [cache-hunter](https://github.com/co-l/cache-hunter), a
proxy that keeps every request body, so a cache break can be byte-diffed in its UI
(http://localhost:4000). Start it first with `just hunter` (needs a clone with `npm install`).

The sandbox (`scenario/sandbox.sh`) copies the real store with every project, session and queue
removed, so nothing resumes and no branch lands in a real repository; settings and MCP servers stay,
and the local endpoint in Settings (which wins over `FENNEC_LLM_URL`) is pointed at the proxy.
`mcp_bench/extract.py` reads the frozen prompt and MCP schemas from the store; the extracted files
are private and git-ignored.

```bash
just --list
```

The `*.brief.md` / `brief.md` files are the specs the scripts were generated from by a local model
(qwen3.8-27b), then reviewed and patched by Claude Code.
