"""Probe the inference server's prefix cache: does a conversation keep its cache
after the turn ends, after an unrelated request, after idle time?"""
import json, sys, time, urllib.request, random
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/v1"
MODEL = json.load(urllib.request.urlopen(URL + "/models"))["data"][0]["id"]

def call(messages):
    req = {"model": MODEL, "messages": messages, "max_tokens": 400, "temperature": 0}
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        URL + "/chat/completions", json.dumps(req).encode(), {"Content-Type": "application/json"})))
    u, m = r["usage"], r["choices"][0]["message"]
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    reply = {"role": "assistant", "content": m.get("content") or ""}
    if m.get("reasoning_content"): reply["reasoning_content"] = m["reasoning_content"]
    return u["prompt_tokens"], cached, u["completion_tokens"], reply

def filler(tag):
    rnd = random.Random(tag + str(time.time()))
    return " ".join(rnd.choice(["alpha", "beta", "gamma", "delta", "omega", "sigma"]) + str(rnd.randint(0, 999)) for _ in range(2000))

def conversation(tag):
    return [{"role": "user", "content": f"Here is a list:\n{filler(tag)}\n\nReply with one short sentence."}]

def step(label, conv, expect_from):
    p, c, g, reply = call(conv)
    print(f"{label:38} prompt={p:6} cached={c:6} expected~{expect_from:6} {'OK' if c >= expect_from - 64 else 'MISS'}")
    conv.append(reply)
    return p + g

conv = conversation("a")
end = step("1. first request", conv, 0)
conv.append({"role": "user", "content": "Another short sentence."})
end = step("2. next turn, right away", conv, end)
conv.append({"role": "user", "content": "Another short sentence."})
call(conversation("other"))
end = step("3. next turn, after another request", conv, end)
conv.append({"role": "user", "content": "Another short sentence."})
time.sleep(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
end = step("4. next turn, after idle", conv, end)
