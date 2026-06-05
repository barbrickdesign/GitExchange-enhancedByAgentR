#!/usr/bin/env python3
"""AgentR self-healing checks and lightweight automated repairs."""

from __future__ import annotations

import os
import time as _time

from utils import (
    load_config,
    get_governing_token,
    load_market,
    save_market,
    list_traders,
    load_trader,
    save_trader,
    update_trader_stats,
    validate_state,
    now_iso,
    log_engine_run,
)


def repair_market_structure(market: dict, config: dict) -> tuple[dict, bool]:
    """Repair common structural issues in market state."""
    changed = False
    governing_token = get_governing_token(config)
    if not isinstance(market, dict):
        return {
            "stocks": {},
            "market_status": "open",
            "last_updated": now_iso(),
            "total_market_cap": 0,
            "governing_token": governing_token,
        }, True

    stocks = market.setdefault("stocks", {})
    for ticker, stock in stocks.items():
        if not isinstance(stock, dict):
            stocks[ticker] = {}
            stock = stocks[ticker]
            changed = True

        shares = stock.get("shares_outstanding", 500)
        if not isinstance(shares, (int, float)) or shares <= 0:
            stock["shares_outstanding"] = 500
            shares = 500
            changed = True

        price = stock.get("price", 0) or 0
        if price < 0:
            stock["price"] = 0
            price = 0
            changed = True

        if stock.get("prev_price") is None:
            stock["prev_price"] = price
            changed = True

        computed_cap = round(price * shares, 2)
        if stock.get("market_cap") != computed_cap:
            stock["market_cap"] = computed_cap
            changed = True

        stock.setdefault("volume_24h", 0)
        stock.setdefault("change_pct", 0.0)
        stock.setdefault("tags", [])
        if stock.get("governing_token") != governing_token:
            stock["governing_token"] = governing_token
            changed = True

    computed_total = round(sum(s.get("market_cap", 0) for s in stocks.values()), 2)
    if market.get("total_market_cap") != computed_total:
        market["total_market_cap"] = computed_total
        changed = True

    if not market.get("last_updated"):
        market["last_updated"] = now_iso()
        changed = True

    if market.get("governing_token") != governing_token:
        market["governing_token"] = governing_token
        changed = True

    market.setdefault("market_status", "open")
    return market, changed


def repair_traders(market: dict) -> int:
    """Recalculate all trader stats to heal drifted balances/stats."""
    updated = 0
    for username in list_traders():
        trader = load_trader(username)
        before = (trader.get("total_value"), trader.get("pnl"), trader.get("pnl_pct"))
        update_trader_stats(trader, market)
        after = (trader.get("total_value"), trader.get("pnl"), trader.get("pnl_pct"))
        if before != after:
            save_trader(username, trader)
            updated += 1
    return updated


def run_self_heal(strict: bool = False) -> dict:
    """Run full self-heal pass and return telemetry details."""
    start = _time.time()
    before_errors = validate_state()
    config = load_config()

    market = load_market()
    market, market_changed = repair_market_structure(market, config)
    if market_changed:
        save_market(market)

    traders_updated = repair_traders(market)
    after_errors = validate_state()

    details = {
        "errors_before": len(before_errors),
        "errors_after": len(after_errors),
        "market_repaired": market_changed,
        "traders_repaired": traders_updated,
        "strict": strict,
    }
    log_engine_run("self_heal", _time.time() - start, details)

    if strict and after_errors:
        raise RuntimeError(f"Self-heal could not resolve all issues: {after_errors[:5]}")

    return details


def main() -> None:
    strict = os.environ.get("AGENTR_STRICT_HEAL", "false").lower() == "true"
    details = run_self_heal(strict=strict)
    print(
        "Self-heal complete — "
        f"errors {details['errors_before']} -> {details['errors_after']}, "
        f"market_repaired={details['market_repaired']}, "
        f"traders_repaired={details['traders_repaired']}"
    )


if __name__ == "__main__":
    main()
