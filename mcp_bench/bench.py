#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict

import jsonschema

DEFAULT_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "qwen3.8-27b"
TIMEOUT_SECONDS = 300
MAX_ROUNDS = 5

TASKS = [
    {
        "id": "mem0-search",
        "prompt": "Search my memories for what I said about commit conventions.",
        "expected": ("mem0", "search_memories"),
        "check": lambda args: bool(args.get("query")),
    },
    {
        "id": "mem0-add",
        "prompt": "Remember this durably: fennec's test server runs on port 7790.",
        "expected": ("mem0", "add_memory"),
        "check": lambda args: "7790" in str(args.get("text", "")),
    },
    {
        "id": "mem0-delete",
        "prompt": "Delete the memory with id 4f2a-99.",
        "expected": ("mem0", "delete_memory"),
        "check": lambda args: args.get("memory_id") == "4f2a-99",
    },
    {
        "id": "notion-search",
        "prompt": "Find my Notion page about the Q1 roadmap.",
        "expected": ("notion", "search"),
        "check": lambda args: True,
    },
    {
        "id": "notion-fetch",
        "prompt": "Fetch the Notion page https://www.notion.so/Cache-tests-1a2b3c4d5e6f and summarize it.",
        "expected": ("notion", "fetch"),
        "check": lambda args: "1a2b3c4d5e6f" in str(args.get("id", "")),
    },
    {
        "id": "notion-create",
        "prompt": "Create a private Notion page titled 'Cache test' whose content is the line 'hello'.",
        "expected": ("notion", "create-pages"),
        "check": lambda args: True,
    },
]

META_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_tools",
            "description": (
                "Returns the full JSON schemas of an MCP server's tools. "
                "Call it before mcp_call when you do not know a tool's exact parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                },
                "required": ["server"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": (
                "Calls a tool of an enabled MCP server. `tool` is the tool name as listed in the catalog, "
                "`arguments` must match its schema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server", "tool", "arguments"],
            },
        },
    },
]


def to_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def first_sentence(text):
    if not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1].isspace()):
            return text[:i].strip()
    return text


def param_signature(schema):
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return ""
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    parts = []
    for name in props.keys():
        if name in required:
            parts.append(name)
        else:
            parts.append(f"{name}?")
    return ", ".join(parts)


def function_part(tool_def):
    if isinstance(tool_def, dict) and "function" in tool_def:
        return tool_def.get("function", {}) or {}
    return tool_def if isinstance(tool_def, dict) else {}


def extract_mcp_tool_name(native_name, server):
    if not native_name:
        return ""
    prefix = f"{server}_"
    if native_name.startswith(prefix):
        rest = native_name[len(prefix):]
        tool_prefix = f"{server}-"
        if rest.startswith(tool_prefix):
            return rest[len(tool_prefix):]
        return rest
    return native_name


def load_inputs(base_path, mcp_path):
    with open(base_path, "r", encoding="utf-8") as f:
        base = json.load(f)
    with open(mcp_path, "r", encoding="utf-8") as f:
        mcp = json.load(f)

    system = base.get("system", "") or ""
    base_tools = base.get("tools", []) or []

    servers = {}
    mcp_defs_by_server = {}
    mcp_by_native = {}
    mcp_by_server_tool = {}

    for server, defs in mcp.items():
        defs = defs or []
        mcp_defs_by_server[server] = defs
        server_tools = []
        for tool_def in defs:
            func = function_part(tool_def)
            native_name = func.get("name", "")
            tool_name = extract_mcp_tool_name(native_name, server)
            schema = func.get("parameters", {})
            if not isinstance(schema, dict):
                schema = {}
            entry = {
                "name": tool_name,
                "description": func.get("description", "") or "",
                "parameters": schema,
                "native_name": native_name,
            }
            server_tools.append(entry)
            mcp_by_native[native_name] = (server, tool_name, schema)
            mcp_by_server_tool[(server, tool_name)] = entry
        servers[server] = server_tools

    return system, base_tools, servers, mcp_defs_by_server, mcp_by_native, mcp_by_server_tool


META_TOOLS2 = [
    {"type": "function", "function": {"name": "mcp_tools",
        "description": "Returns the JSON schemas of the named tools of an MCP server. Call it before mcp_call when a tool's parameters are not listed in the catalog.",
        "parameters": {"type": "object", "properties": {"server": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names from the catalog."}},
            "required": ["server", "tools"]}}},
] + [t for t in META_TOOLS if t["function"]["name"] == "mcp_call"]


def build_catalog2(servers):
    lines = [
        "## MCP servers enabled in this session",
        "Call them with mcp_call(server, tool, arguments). Tools listed without parameters: get their schemas first with mcp_tools(server, [tool names]).",
    ]
    for server, tools in servers.items():
        lines.append(f"- {server}:")
        small = len(tools) <= 8
        for tool in tools:
            desc = first_sentence(tool.get("description", ""))
            if small:
                lines.append(f"  - `{tool['name']}({param_signature(tool.get('parameters', {}))})` — {desc}")
            else:
                lines.append(f"  - `{tool['name']}` — {desc}")
    return "<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>"


def build_catalog(servers):
    lines = [
        "## MCP servers enabled in this session",
        "Call them with mcp_call(server, tool, arguments); mcp_tools(server) returns their full schemas.",
    ]

    if "mem0" in servers:
        lines.append("- mem0:")
        for tool in servers["mem0"]:
            desc = first_sentence(tool.get("description", ""))
            params = param_signature(tool.get("parameters", {}))
            lines.append(f"  - `{tool['name']}({params})` — {desc}")
    else:
        lines.append("- mem0: no tools")

    notion_count = len(servers.get("notion", []))
    lines.append(
        f"- notion: {notion_count} tools (workspace pages, databases, search). "
        'Call mcp_tools("notion") to list them.'
    )

    return "<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>"


def parse_json_object(raw):
    if isinstance(raw, dict):
        return raw, True
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}, False
        try:
            obj = json.loads(text)
        except Exception:
            return {}, False
        if isinstance(obj, dict):
            return obj, True
        return {}, False
    return {}, False


def validate_against_schema(schema, args):
    if not isinstance(args, dict):
        return False, "arguments must be an object"
    if not isinstance(schema, dict):
        schema = {}
    schema = {k: v for k, v in schema.items() if k != "$schema"}
    try:
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
    except Exception as exc:
        return False, f"schema validation failed: {exc}"
    if errors:
        return False, errors[0].message
    return True, ""


def chat_completion(url, model, messages, tools):
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "max_tokens": 8000,
        "reasoning_effort": "xhigh",
        "temperature": 0.6,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_bench(config, task, rep, system, base_tools, servers, mcp_defs_by_server,
              mcp_by_native, mcp_by_server_tool, catalog, url, model):
    record = {
        "config": config,
        "task": task["id"],
        "rep": rep,
        "success": False,
        "rounds": 0,
        "invalid_calls": 0,
        "schema_fetches": 0,
        "wrong_tool_calls": 0,
        "first_prompt_tokens": 0,
        "first_cached_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "error": None,
    }

    if config == "native":
        tools = list(base_tools)
        for server in ("mem0", "notion"):
            tools.extend(mcp_defs_by_server.get(server, []))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": task["prompt"]},
        ]
    else:
        tools = list(base_tools) + (META_TOOLS2 if config == "lazy2" else META_TOOLS)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": catalog[config]},
            {"role": "user", "content": task["prompt"]},
        ]

    for round_num in range(1, MAX_ROUNDS + 1):
        try:
            data = chat_completion(url, model, messages, tools)
        except Exception as exc:
            record["error"] = str(exc)
            break

        record["rounds"] = round_num

        usage = data.get("usage", {}) or {}
        prompt_tokens = to_int(usage.get("prompt_tokens"))
        completion_tokens = to_int(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        cached_tokens = to_int(details.get("cached_tokens")) if isinstance(details, dict) else 0

        record["total_prompt_tokens"] += prompt_tokens
        record["total_completion_tokens"] += completion_tokens
        if round_num == 1:
            record["first_prompt_tokens"] = prompt_tokens
            record["first_cached_tokens"] = cached_tokens

        try:
            message = data["choices"][0]["message"]
        except Exception as exc:
            record["error"] = f"bad response: {exc}"
            break

        tool_calls = message.get("tool_calls") or []
        assistant_msg = {"role": "assistant", "content": message.get("content")}

        if tool_calls:
            normalized_calls = []
            for idx, tc in enumerate(tool_calls):
                func = tc.get("function", {}) or {}
                name = func.get("name", "")
                raw_args = func.get("arguments", "")
                if raw_args is None:
                    raw_args = "{}"
                if not isinstance(raw_args, str):
                    raw_args = json.dumps(raw_args, ensure_ascii=False)
                normalized_calls.append({
                    "id": tc.get("id") or f"call_{round_num}_{idx}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": raw_args,
                    },
                })
            assistant_msg["tool_calls"] = normalized_calls

        messages.append(assistant_msg)

        if not tool_calls:
            break

        for idx, tc in enumerate(tool_calls):
            tc_id = tc.get("id") or f"call_{round_num}_{idx}"
            func = tc.get("function", {}) or {}
            name = func.get("name", "")
            raw_args = func.get("arguments", "")

            if name == "mcp_tools":
                record["schema_fetches"] += 1

            args, args_ok = parse_json_object(raw_args)
            result = None

            if not args_ok:
                record["invalid_calls"] += 1
                result = {"error": "invalid arguments: arguments are not a JSON object"}
            elif name == "mcp_tools":
                server = args.get("server")
                if isinstance(server, str) and server in servers:
                    wanted = args.get("tools")
                    result = [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": tool["parameters"],
                        }
                        for tool in servers[server]
                        if not isinstance(wanted, list) or tool["name"] in wanted
                    ]
                else:
                    result = {"error": f"unknown server: {server}"}
            elif name == "mcp_call":
                server = args.get("server")
                tool = args.get("tool")
                if not isinstance(server, str) or not isinstance(tool, str):
                    record["invalid_calls"] += 1
                    result = {"error": "invalid arguments: server and tool must be strings"}
                else:
                    inner = args.get("arguments")
                    if isinstance(inner, str):
                        try:
                            while isinstance(inner, str):
                                inner = json.loads(inner)
                        except Exception as exc:
                            record["invalid_calls"] += 1
                            inner = None
                            result = {"error": f"invalid arguments: {exc}"}

                    if result is None:
                        if not isinstance(inner, dict):
                            record["invalid_calls"] += 1
                            result = {"error": "invalid arguments: arguments must be an object"}
                        else:
                            entry = mcp_by_server_tool.get((server, tool))
                            if entry is None:
                                record["invalid_calls"] += 1
                                result = {"error": f"unknown tool: {server}/{tool}"}
                            else:
                                valid, err = validate_against_schema(entry["parameters"], inner)
                                if not valid:
                                    record["invalid_calls"] += 1
                                    result = {"error": f"invalid arguments: {err}"}
                                else:
                                    result = {"ok": True, "result": f"{tool} done"}
                                    if server == task["expected"][0] and tool == task["expected"][1]:
                                        try:
                                            if task["check"](inner):
                                                record["success"] = True
                                        except Exception:
                                            pass
                                    else:
                                        record["wrong_tool_calls"] += 1
            elif name in mcp_by_native:
                server, tool, schema = mcp_by_native[name]
                valid, err = validate_against_schema(schema, args)
                if not valid:
                    record["invalid_calls"] += 1
                    result = {"error": f"invalid arguments: {err}"}
                else:
                    result = {"ok": True, "result": f"{tool} done"}
                    if server == task["expected"][0] and tool == task["expected"][1]:
                        try:
                            if task["check"](args):
                                record["success"] = True
                        except Exception:
                            pass
                    else:
                        record["wrong_tool_calls"] += 1
            else:
                result = {"error": "not available in this test"}

            if result is None:
                result = {"error": "unknown tool"}

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(result, ensure_ascii=False),
            })

            if record["success"]:
                break

        if record["success"]:
            break

    if os.environ.get("DUMP"):
        record["messages"] = messages[3:] if config != "native" else messages[2:]
    return record


def mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def print_table(title, rows):
    headers = ["task", "success", "rounds", "invalid", "schema", "first_prompt"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    print(title)
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    print()


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Benchmark native vs lazy MCP tool loading.")
    parser.add_argument("--base", default=os.path.join(script_dir, "base.json"))
    parser.add_argument("--mcp", default=os.path.join(script_dir, "mcp_tools.json"))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--configs", default="native,lazy")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--out", default="results.jsonl")
    args = parser.parse_args()

    if args.reps < 0:
        args.reps = 0

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    configs = [c for c in configs if c in ("native", "lazy", "lazy2")]
    if not configs:
        configs = ["native", "lazy"]

    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if task_ids:
        tasks = [t for t in TASKS if t["id"] in task_ids]
        if not tasks:
            print(f"Unknown task ids: {args.tasks}", file=sys.stderr)
            sys.exit(1)
    else:
        tasks = TASKS

    try:
        (
            system,
            base_tools,
            servers,
            mcp_defs_by_server,
            mcp_by_native,
            mcp_by_server_tool,
        ) = load_inputs(args.base, args.mcp)
    except Exception as exc:
        print(f"Failed to load inputs: {exc}", file=sys.stderr)
        sys.exit(1)

    catalog = {"lazy": build_catalog(servers), "lazy2": build_catalog2(servers)}
    records = []

    for config in configs:
        for task in tasks:
            for rep in range(1, args.reps + 1):
                record = run_bench(
                    config=config,
                    task=task,
                    rep=rep,
                    system=system,
                    base_tools=base_tools,
                    servers=servers,
                    mcp_defs_by_server=mcp_defs_by_server,
                    mcp_by_native=mcp_by_native,
                    mcp_by_server_tool=mcp_by_server_tool,
                    catalog=catalog,
                    url=args.url,
                    model=args.model,
                )
                records.append(record)

                out_dir = os.path.dirname(args.out)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

                progress = (
                    f"[{record['config']}/{record['task']}/rep{record['rep']}] "
                    f"success={record['success']} rounds={record['rounds']} "
                    f"invalid={record['invalid_calls']} schema={record['schema_fetches']} "
                    f"wrong={record['wrong_tool_calls']} prompt={record['total_prompt_tokens']} "
                    f"cached={record['first_cached_tokens']}"
                )
                if record["error"]:
                    progress += f" error={record['error']}"
                print(progress, flush=True)

    for config in configs:
        rows = []
        for task in tasks:
            runs = [r for r in records if r["config"] == config and r["task"] == task["id"]]
            n = len(runs)
            success_rate = (sum(1 for r in runs if r["success"]) / n) if n else 0.0
            rows.append([
                task["id"],
                f"{success_rate:.1%}",
                f"{mean([r['rounds'] for r in runs]):.1f}",
                f"{mean([r['invalid_calls'] for r in runs]):.1f}",
                f"{mean([r['schema_fetches'] for r in runs]):.1f}",
                f"{mean([r['first_prompt_tokens'] for r in runs]):.1f}",
            ])
        print_table(f"Config: {config}", rows)

        all_runs = [r for r in records if r["config"] == config]
        n = len(all_runs)
        success_rate = (sum(1 for r in all_runs if r["success"]) / n) if n else 0.0
        print(
            f"Total {config}: runs={n} success={success_rate:.1%} "
            f"rounds={mean([r['rounds'] for r in all_runs]):.1f} "
            f"invalid={mean([r['invalid_calls'] for r in all_runs]):.1f} "
            f"schema={mean([r['schema_fetches'] for r in all_runs]):.1f} "
            f"first_prompt={mean([r['first_prompt_tokens'] for r in all_runs]):.1f}\n"
        )


if __name__ == "__main__":
    main()
