# Cache and MCP tooling for fennec: run against a sandbox copy, never the real store.

fennec := env_var_or_default("FENNEC_BIN", env_var("HOME") + "/workspace/fennec/target/release/fennec")
hunter := env_var_or_default("CACHE_HUNTER", env_var("HOME") + "/workspace/cache-hunter-src")
llm := env_var_or_default("LLM_URL", "http://127.0.0.1:8000/v1")

# Cache health of the real store's recent sessions (read-only)
watch *args:
    python3 -I cache_watch.py {{args}}

# Does the inference server keep its prefix cache?
probe:
    python3 -I probe.py {{llm}}

# cache-hunter proxy (:8787 -> the inference server, UI on :4000), logs every request body
hunter:
    cd {{hunter}} && TARGET_HOST=127.0.0.1 TARGET_PORT=8000 npx tsx src/index.ts

# Throwaway fennec on :7790 behind cache-hunter (`just hunter` first), captured: the composer scenario, then its cache report
composer:
    scenario/sandbox.sh {{fennec}} 7790 http://127.0.0.1:8787/v1
    curl -sf -X POST localhost:4000/api/capture/start > /dev/null
    python3 -I scenario/composer.py 7790; curl -sf -X POST localhost:4000/api/capture/stop > /dev/null; kill $(cat /tmp/fennec-cache-sandbox/pid)
    -FENNEC_DB=/tmp/fennec-cache-sandbox/fennec.db python3 -I cache_watch.py -v -a
    python3 -I scenario/hunter_diff.py

# Lazy vs native MCP tools: extract inputs from the real store, then bench
bench *args:
    python3 -I mcp_bench/extract.py
    python3 -I mcp_bench/bench.py {{args}}
