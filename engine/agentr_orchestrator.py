#!/usr/bin/env python3
"""AgentR orchestrator for end-to-end automation with recovery steps."""

from __future__ import annotations

import time as _time

import event_engine
import price_engine
import render_engine
from self_heal import run_self_heal
from utils import log_engine_run


def _run_step(name: str, fn, retries: int = 2) -> dict:
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            fn()
            return {"name": name, "status": "ok", "attempt": attempt}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"[{name}] failed (attempt {attempt}/{retries}): {last_error}")
            if attempt < retries:
                print(f"[{name}] invoking self-heal before retry...")
                run_self_heal(strict=False)
    return {"name": name, "status": "failed", "attempt": retries, "error": last_error}


def main() -> None:
    start = _time.time()

    print("AgentR orchestrator start")
    results = []

    results.append(_run_step("self_heal_preflight", lambda: run_self_heal(strict=False), retries=1))
    results.append(_run_step("price_engine", price_engine.main, retries=2))
    results.append(_run_step("event_engine", event_engine.main, retries=2))
    results.append(_run_step("render_engine", render_engine.main, retries=2))
    results.append(_run_step("self_heal_post", lambda: run_self_heal(strict=True), retries=1))

    failed = [r for r in results if r.get("status") != "ok"]
    status = "failed" if failed else "ok"

    details = {
        "status": status,
        "steps": results,
    }
    log_engine_run("agentr_orchestrator", _time.time() - start, details)

    print("AgentR orchestrator summary")
    for r in results:
        if r.get("status") == "ok":
            print(f"  ✅ {r['name']} (attempt {r['attempt']})")
        else:
            print(f"  ❌ {r['name']} ({r.get('error', 'unknown error')})")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
