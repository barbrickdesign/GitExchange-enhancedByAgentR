"""Solana RPC helpers for on-chain trade payment verification."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import requests

WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SIGNATURE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{88}$")


class SolanaRPCError(RuntimeError):
    """Raised when Solana RPC calls fail or payloads are invalid."""


def _rpc_request(rpc_url: str, method: str, params: list, timeout: int = 20) -> dict:
    payload = {"jsonrpc": "2.0", "id": int(time.time()), "method": method, "params": params}
    try:
        res = requests.post(rpc_url, json=payload, timeout=timeout)
        res.raise_for_status()
        data = res.json()
    except Exception as exc:
        raise SolanaRPCError(f"RPC request failed for {method}: {exc}") from exc

    if "error" in data:
        message = data["error"].get("message", "unknown RPC error")
        raise SolanaRPCError(f"RPC error for {method}: {message}")

    return data.get("result")


def is_valid_wallet(value: str) -> bool:
    return bool(value and WALLET_RE.match(value.strip()))


def is_valid_signature(value: str) -> bool:
    return bool(value and SIGNATURE_RE.match(value.strip()))


def sol_to_lamports(sol: float) -> int:
    return max(0, int(round(sol * 1_000_000_000)))


def usd_to_lamports(usd: float, usd_per_sol: float) -> int:
    if usd_per_sol <= 0:
        raise SolanaRPCError("usd_per_sol must be > 0")
    return sol_to_lamports(usd / usd_per_sol)


def get_balance_lamports(wallet: str, rpc_url: str) -> int:
    result = _rpc_request(rpc_url, "getBalance", [wallet, {"commitment": "confirmed"}])
    return int(result.get("value", 0))


def get_transaction(signature: str, rpc_url: str) -> dict:
    params = [
        signature,
        {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        },
    ]
    tx = _rpc_request(rpc_url, "getTransaction", params)
    if not tx:
        raise SolanaRPCError("transaction not found or not yet confirmed")
    return tx


def _extract_transfer_lamports(tx: dict, sender: str | None, destination: str) -> int:
    message = tx.get("transaction", {}).get("message", {})
    instructions = message.get("instructions", [])
    matched = 0

    for ix in instructions:
        parsed = ix.get("parsed") if isinstance(ix, dict) else None
        if not parsed or parsed.get("type") != "transfer":
            continue

        info = parsed.get("info", {})
        src = info.get("source", "")
        dst = info.get("destination", "")
        lamports = int(info.get("lamports", 0))

        if dst != destination:
            continue
        if sender and src != sender:
            continue
        matched += lamports

    return matched


def verify_payment_signature(
    signature: str,
    sender_wallet: str,
    destination_wallet: str,
    min_lamports: int,
    rpc_url: str,
    max_tx_age_seconds: int = 3600,
) -> dict:
    """Verify a confirmed SOL transfer to treasury wallet meets required lamports."""
    if not is_valid_signature(signature):
        raise SolanaRPCError("invalid Solana signature format")
    if not is_valid_wallet(sender_wallet):
        raise SolanaRPCError("invalid sender wallet format")
    if not is_valid_wallet(destination_wallet):
        raise SolanaRPCError("invalid destination wallet format")
    if destination_wallet == "11111111111111111111111111111111":
        raise SolanaRPCError("invalid destination wallet: configure a real treasury wallet")

    tx = get_transaction(signature, rpc_url)

    meta = tx.get("meta", {})
    if meta.get("err"):
        raise SolanaRPCError(f"transaction failed on-chain: {meta['err']}")

    block_time = tx.get("blockTime")
    if block_time:
        age = int(datetime.now(timezone.utc).timestamp()) - int(block_time)
        if age > max_tx_age_seconds:
            raise SolanaRPCError(
                f"transaction too old ({age}s). Submit a payment within the last {max_tx_age_seconds}s"
            )

    lamports = _extract_transfer_lamports(tx, sender_wallet, destination_wallet)
    if lamports < min_lamports:
        raise SolanaRPCError(
            f"insufficient on-chain payment: received {lamports} lamports, require at least {min_lamports}"
        )

    return {
        "signature": signature,
        "sender": sender_wallet,
        "destination": destination_wallet,
        "lamports": lamports,
        "slot": tx.get("slot"),
        "block_time": block_time,
    }
