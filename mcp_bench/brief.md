Write ONE self-contained Python 3 script `bench.py` (stdlib + `jsonschema` only, no other deps). Output ONLY the script in a single ```python block.

## Goal
Measure whether a local LLM calls MCP tools as well when they are lazy-loaded through two meta-tools as when they are declared natively.

## Inputs (files next to the script, paths given as CLI args with these defaults)
- `base.json`: `{"system": str, "tools": [OpenAI tool defs]}` — the agent's system prompt and its 17 built-in tools.
- `mcp_tools.json`: `{"mem0": [tool defs], "notion": [tool defs]}`. Each def is OpenAI format; native names look like `mem0_mem0-search_memories` / `notion_notion-fetch`: server = text before the first `_`, MCP tool name = the rest after `<server>_<server>-` (e.g. `search_memories`, `fetch`). Parameters are JSON Schemas.

## Endpoint
OpenAI-compatible `POST {URL}/chat/completions`, URL default `http://127.0.0.1:8000/v1`, model default `qwen3.8-27b`. Body: model, messages, tools, `max_tokens: 8000`, `reasoning_effort: "xhigh"`, `temperature: 0.6`. Use urllib, timeout 300 s. Read `choices[0].message` (`content`, `tool_calls`, ignore `reasoning_content`) and `usage` (`prompt_tokens`, `completion_tokens`, and `prompt_tokens_details.cached_tokens` if present).

## Two configs
- `native`: tools = base tools + all mem0 + all notion defs as-is. Messages: system, then the task as user message.
- `lazy`: tools = base tools + two meta-tools:
  - `mcp_tools` {server: string (required)} — "Returns the full JSON schemas of an MCP server's tools. Call it before mcp_call when you do not know a tool's exact parameters."
  - `mcp_call` {server: string, tool: string, arguments: object} all required — "Calls a tool of an enabled MCP server. `tool` is the tool name as listed in the catalog, `arguments` must match its schema."
  Messages: system, then a user message holding the catalog wrapped in `<system-reminder>…</system-reminder>`:
  ```
  ## MCP servers enabled in this session
  Call them with mcp_call(server, tool, arguments); mcp_tools(server) returns their full schemas.
  - mem0: <one line per tool: `name(param1, param2?)` — first sentence of description>; required params bare, optional ones with `?`
  - notion: 34 tools (workspace pages, databases, search). Call mcp_tools("notion") to list them.
  ```
  then the task as a separate user message.

## Simulated tool execution (no real MCP)
- `mcp_tools(server)` → JSON list of `{name, description, parameters}` for that server (MCP names, not native names); unknown server → error.
- A call to an MCP tool (native by name, or via mcp_call server+tool) → validate `arguments` with `jsonschema.Draft202012Validator` against its schema (drop the `$schema` key first). Invalid → tool result `{"error": "invalid arguments: <first error message>"}` and count an invalid call. Unknown tool → error and count invalid. Valid → fake result `{"ok": true, "result": "<tool> done"}`.
- Any built-in tool call → result `{"error": "not available in this test"}`.
- `arguments` may arrive as a JSON string (tool_calls function.arguments is a string): parse it; for mcp_call, if `arguments` inside is itself a string, json-parse it too; parse failure counts as invalid.

## Tasks (list of dicts in the script)
Each: id, prompt, expected (server, mcp tool name), check (lambda on parsed args → bool).
1. mem0-search: "Search my memories for what I said about commit conventions." → mem0/search_memories, query non-empty.
2. mem0-add: "Remember this durably: fennec's test server runs on port 7790." → mem0/add_memory, "7790" in text.
3. mem0-delete: "Delete the memory with id 4f2a-99." → mem0/delete_memory, memory_id == "4f2a-99".
4. notion-search: "Find my Notion page about the Q1 roadmap." → notion/search, any args valid.
5. notion-fetch: "Fetch the Notion page https://www.notion.so/Cache-tests-1a2b3c4d5e6f and summarize it." → notion/fetch, "1a2b3c4d5e6f" in id.
6. notion-create: "Create a private Notion page titled 'Cache test' whose content is the line 'hello'." → notion/create-pages, any valid args.

## Loop per (config, task, repetition)
Up to 5 model rounds. After each response append the assistant message (with tool_calls) and one `tool` message per call. Stop on success: a VALID call to the expected tool whose check passes. Stop also if the model answers with no tool call. Record: success (bool), rounds (model calls made), invalid_calls, schema_fetches (mcp_tools calls), wrong_tool_calls (valid calls to another MCP tool), prompt_tokens of round 1, total prompt and completion tokens, first-round cached tokens.

## CLI and output
`python3 bench.py [--reps 3] [--configs native,lazy] [--tasks id1,id2] [--out results.jsonl]`. Append one JSON line per run to --out, print a progress line per run, and at the end print a table per config × task: success rate, mean rounds, mean invalid calls, mean schema fetches, mean first-round prompt tokens. Then a total line per config. Any HTTP error on a run: record it as `error` and continue.
