"""The nine suite tasks: what the model is asked, and how each is checked."""
from __future__ import annotations

import datetime as dt
import json
import re

TAIL = " Use the browser tools only (no exec, no computer_action). Stop when the task is done."

PROMPTS = {
    "login": "Open {base}/login in the browser. Sign in with username tester and password orange-42 (a test "
             "account of this local app), turn on Email notifications and save the settings.",
    "shop": "On {base}/shop, add the cheapest item in the Garden category to the cart, then check out with full "
            "name Ada Lovelace and Express shipping.",
    "wizard": "Sign up at {base}/wizard with email grace@example.com and the Team plan, accepting the terms.",
    "feed": "On {base}/feed, click the Select button of Item 73.",
    "newtab": "On {base}/docs, get the API key from the developer page (its link opens a new tab) and save it in "
              "the API key field on the docs page.",
    "iframe": "On {base}/embed, pay 25 with card holder Grace Hopper.",
    "extract": "On {base}/orders, what is the total of all orders by Bob Ng dated in October 2026? Reply with the "
               "amount.",
    "widget": "On {base}/widget, set the quantity to 4 and apply it.",
    "modal": "On {base}/drafts, delete the draft named 'Old notes' and nothing else.",
}


def check(task, expected, site, final_text):
    if task == "extract":
        numbers = {round(float(n.replace(",", "")), 2) for n in re.findall(r"\d[\d,]*\.?\d*", final_text or "")}
        return [] if round(float(expected["answer"]), 2) in numbers else ["answer"]
    got = site.results.get(task, {})
    return sorted(k for k, v in expected.items()
                  if (str(got.get(k)).strip().casefold() if isinstance(v, str) else got.get(k)) !=
                  (v.casefold() if isinstance(v, str) else v))


def metrics(events_path):
    steps, calls, failed, model_ms, tool_ms, started = 0, 0, 0, 0.0, 0.0, {}
    by_tool, fail_kinds = {}, {}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        d = e.get("data") or {}
        if e["kind"] == "model_response":
            steps += 1
            model_ms += float(d.get("model_execution_ms") or 0)
        elif e["kind"] == "tool_requested":
            calls += 1
            by_tool[d.get("tool")] = by_tool.get(d.get("tool"), 0) + 1
        elif e["kind"] == "tool_started":
            started[d.get("call_id")] = dt.datetime.fromisoformat(e["created_at"])
        elif e["kind"] in {"tool_completed", "tool_failed"}:
            if d.get("call_id") in started:
                tool_ms += (dt.datetime.fromisoformat(e["created_at"]) - started[d["call_id"]]).total_seconds() * 1000
            if e["kind"] == "tool_failed" or d.get("ok") is False:
                failed += 1
                key = f"{d.get('tool')}: {str(d.get('error') or d.get('content') or '')[:60]}"
                fail_kinds[key] = fail_kinds.get(key, 0) + 1
    return {"model_steps": steps, "tool_calls": calls, "failed_calls": failed, "model_ms": round(model_ms),
            "tool_ms": round(tool_ms), "by_tool": by_tool, "failures": fail_kinds}
