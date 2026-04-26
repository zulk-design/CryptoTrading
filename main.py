#!/usr/bin/env python3
"""
BTC/USD Chandelier Stop paper-trading runner for Alpaca.

This script is intentionally paper-only. It refuses to place orders unless
ALPACA_BASE_URL points at Alpaca's paper endpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
STATE_FILE = STATE_DIR / "btcusd_state.json"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"
REPORTS_LOG = LOG_DIR / "reports.md"

SYMBOL = "BTC/USD"
POSITION_SYMBOLS = ("BTCUSD", "BTC/USD")
DATA_BASE_URL = "https://data.alpaca.markets"
CRYPTO_LOCATION = "us"
TIMEFRAME = "5Min"
BARS_LIMIT = 200

CHAND_PERIOD = 22
ATR_MULTIPLIER = 3.0
MAX_CASH_FRACTION = 0.10
MAX_DAILY_LOSS_CASH_FRACTION = 0.50
STOP_LOSS_PCT = 0.05
TRAIL_TRIGGER_PCT = 0.10
TRAIL_DISTANCE_PCT = 0.05
DIP_TIERS = (
    {"name": "drop_30", "drop": 0.30, "multiplier": 3.0},
    {"name": "drop_20", "drop": 0.20, "multiplier": 2.0},
)
REPORT_INTERVAL_SECONDS = 24 * 60 * 60
TRADING_TIMEZONE = "Asia/Jakarta"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_day_key() -> str:
    return datetime.now(ZoneInfo(TRADING_TIMEZONE)).date().isoformat()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def config() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    base_url = require_env("ALPACA_BASE_URL").rstrip("/")
    if base_url.endswith("/v2"):
        base_url = base_url[:-3].rstrip("/")
    if "paper-api.alpaca.markets" not in base_url:
        raise RuntimeError(
            "Refusing to trade: ALPACA_BASE_URL must be Alpaca paper trading "
            "(https://paper-api.alpaca.markets)."
        )
    return {
        "base_url": base_url,
        "key_id": require_env("ALPACA_API_KEY_ID"),
        "secret_key": require_env("ALPACA_API_SECRET_KEY"),
    }


def request_json(
    method: str,
    url: str,
    cfg: dict[str, str],
    payload: dict[str, Any] | None = None,
    ok_missing: bool = False,
) -> Any:
    headers = {
        "APCA-API-KEY-ID": cfg["key_id"],
        "APCA-API-SECRET-KEY": cfg["secret_key"],
        "Accept": "application/json",
        "User-Agent": "btcusd-chandelier-paper-bot/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        if ok_missing and exc.code == 404:
            return None
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc


def get_account(cfg: dict[str, str]) -> dict[str, Any]:
    return request_json("GET", f"{cfg['base_url']}/v2/account", cfg)


def get_position(cfg: dict[str, str]) -> dict[str, Any] | None:
    for symbol in POSITION_SYMBOLS:
        encoded = urllib.parse.quote(symbol, safe="")
        position = request_json(
            "GET",
            f"{cfg['base_url']}/v2/positions/{encoded}",
            cfg,
            ok_missing=True,
        )
        if position:
            return position
    return None


def get_open_orders(cfg: dict[str, str]) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"status": "open", "symbols": SYMBOL})
    return request_json("GET", f"{cfg['base_url']}/v2/orders?{params}", cfg)


def get_bars(cfg: dict[str, str]) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "symbols": SYMBOL,
            "timeframe": TIMEFRAME,
            "limit": str(BARS_LIMIT),
            "sort": "asc",
        }
    )
    url = f"{DATA_BASE_URL}/v1beta3/crypto/{CRYPTO_LOCATION}/bars?{params}"
    data = request_json("GET", url, cfg)
    bars = data.get("bars", {}).get(SYMBOL, [])
    if len(bars) < CHAND_PERIOD + 2:
        raise RuntimeError(f"Need at least {CHAND_PERIOD + 2} bars, got {len(bars)}")
    return bars


def true_range(current: dict[str, Any], previous: dict[str, Any]) -> float:
    high = float(current["h"])
    low = float(current["l"])
    prev_close = float(previous["c"])
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def chandelier_stop(bars: list[dict[str, Any]]) -> tuple[float, float, float]:
    window = bars[-CHAND_PERIOD:]
    highest_high = max(float(bar["h"]) for bar in window)
    ranges = [
        true_range(bars[idx], bars[idx - 1])
        for idx in range(len(bars) - CHAND_PERIOD, len(bars))
    ]
    atr = sum(ranges) / len(ranges)
    stop = highest_high - (ATR_MULTIPLIER * atr)
    last_close = float(bars[-1]["c"])
    return last_close, atr, stop


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    now = utc_now()
    return {
        "started_at": now,
        "last_report_at": now,
        "stop_floor": None,
        "position_basis": None,
        "dip_buys": [],
        "last_order_id": None,
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def place_market_order(
    cfg: dict[str, str],
    side: str,
    notional: float | None = None,
    qty: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": SYMBOL,
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
    }
    if notional is not None:
        payload["notional"] = str(round(notional, 2))
    if qty is not None:
        payload["qty"] = f"{qty:.8f}".rstrip("0").rstrip(".")
    return request_json("POST", f"{cfg['base_url']}/v2/orders", cfg, payload=payload)


def account_cash(account: dict[str, Any]) -> float:
    return max(0.0, float(account.get("cash", "0") or 0))


def account_equity(account: dict[str, Any]) -> float:
    return max(0.0, float(account.get("equity", "0") or 0))


def position_qty(position: dict[str, Any] | None) -> float:
    return float(position.get("qty", "0") or 0) if position else 0.0


def avg_entry(position: dict[str, Any] | None) -> float | None:
    if not position:
        return None
    value = position.get("avg_entry_price")
    return float(value) if value else None


def notional_for(cash: float, multiplier: float) -> float:
    return math.floor(cash * MAX_CASH_FRACTION * multiplier * 100) / 100


def update_daily_risk(state: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    day = local_day_key()
    cash = account_cash(account)
    equity = account_equity(account)
    risk = state.get("daily_risk")
    if not risk or risk.get("date") != day:
        risk = {
            "date": day,
            "start_cash": cash,
            "start_equity": equity,
        }

    max_loss = float(risk["start_cash"]) * MAX_DAILY_LOSS_CASH_FRACTION
    current_loss = max(0.0, float(risk["start_equity"]) - equity)
    risk.update(
        {
            "current_cash": cash,
            "current_equity": equity,
            "max_loss": max_loss,
            "current_loss": current_loss,
            "halted": current_loss >= max_loss,
            "updated_at": utc_now(),
        }
    )
    state["daily_risk"] = risk
    return risk


def update_stop_floor(
    state: dict[str, Any],
    has_position: bool,
    entry_price: float | None,
    price: float,
    chand_stop: float,
) -> tuple[float | None, list[str]]:
    notes: list[str] = []
    if not has_position or not entry_price:
        if state.get("stop_floor") is not None:
            notes.append("cleared_stop_floor_no_position")
        state["stop_floor"] = None
        state["position_basis"] = None
        state["dip_buys"] = []
        return None, notes

    floor_candidates = [entry_price * (1 - STOP_LOSS_PCT), chand_stop]
    if price >= entry_price * (1 + TRAIL_TRIGGER_PCT):
        floor_candidates.append(price * (1 - TRAIL_DISTANCE_PCT))
    new_floor = max(floor_candidates)
    old_floor = state.get("stop_floor")
    if old_floor is not None:
        new_floor = max(float(old_floor), new_floor)
    if old_floor is None or new_floor > float(old_floor):
        notes.append(f"raised_stop_floor_to_{new_floor:.2f}")
    state["stop_floor"] = new_floor
    state["position_basis"] = entry_price
    return new_floor, notes


def should_buy_initial(price: float, chand_stop: float, position: dict[str, Any] | None) -> bool:
    return position is None and price > chand_stop


def dip_tier_to_buy(
    state: dict[str, Any],
    entry_price: float | None,
    price: float,
) -> dict[str, Any] | None:
    if not entry_price:
        return None
    bought = set(state.get("dip_buys", []))
    for tier in DIP_TIERS:
        if tier["name"] not in bought and price <= entry_price * (1 - tier["drop"]):
            return tier
    return None


def summarize_report(account: dict[str, Any], position: dict[str, Any] | None) -> str:
    lines = DECISIONS_LOG.read_text(encoding="utf-8").splitlines() if DECISIONS_LOG.exists() else []
    records = [json.loads(line) for line in lines[-500:] if line.strip()]
    cutoff = time.time() - REPORT_INTERVAL_SECONDS
    recent = [
        row for row in records
        if datetime.fromisoformat(row["timestamp"]).timestamp() >= cutoff
    ]
    orders = [row for row in recent if row.get("order")]
    realized = account.get("last_equity") or "n/a"
    equity = account.get("equity", "n/a")
    cash = account.get("cash", "n/a")
    unrealized = position.get("unrealized_pl") if position else "0"
    qty = position.get("qty") if position else "0"
    avg = position.get("avg_entry_price") if position else "n/a"
    now = utc_now()
    return (
        f"\n## BTCUSD Paper Trading Report - {now}\n\n"
        f"- Decisions reviewed: {len(recent)}\n"
        f"- Orders submitted: {len(orders)}\n"
        f"- Current quantity: {qty}\n"
        f"- Average entry: {avg}\n"
        f"- Cash: {cash}\n"
        f"- Equity: {equity}\n"
        f"- Last equity field: {realized}\n"
        f"- Unrealized P/L: {unrealized}\n"
        f"- Fees/slippage: not provided by this runner\n"
        f"- Continue unchanged: yes, unless errors appear in the decision log\n"
    )


def maybe_write_report(state: dict[str, Any], account: dict[str, Any], position: dict[str, Any] | None) -> bool:
    last = datetime.fromisoformat(state["last_report_at"]).timestamp()
    if time.time() - last < REPORT_INTERVAL_SECONDS:
        return False
    LOG_DIR.mkdir(exist_ok=True)
    with REPORTS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(summarize_report(account, position))
    state["last_report_at"] = utc_now()
    return True


def run_once(no_trade: bool = False) -> dict[str, Any]:
    cfg = config()
    state = load_state()
    account = get_account(cfg)
    position = get_position(cfg)
    open_orders = get_open_orders(cfg)
    bars = get_bars(cfg)
    price, atr, chand_stop = chandelier_stop(bars)

    cash = account_cash(account)
    qty = position_qty(position)
    has_position = qty > 0
    entry = avg_entry(position)
    daily_risk = update_daily_risk(state, account)
    stop_floor, notes = update_stop_floor(state, has_position, entry, price, chand_stop)

    decision = "hold"
    reason = "no_signal"
    order = None

    if open_orders:
        reason = "open_order_exists"
    elif has_position and stop_floor is not None and price <= stop_floor:
        decision = "sell"
        reason = f"price_{price:.2f}_below_stop_floor_{stop_floor:.2f}"
        if not no_trade:
            order = place_market_order(cfg, "sell", qty=qty)
    elif daily_risk["halted"]:
        reason = (
            "daily_loss_limit_reached_"
            f"{daily_risk['current_loss']:.2f}_of_{daily_risk['max_loss']:.2f}"
        )
    elif has_position:
        tier = dip_tier_to_buy(state, entry, price)
        if tier:
            notional = notional_for(cash, tier["multiplier"])
            if notional >= 1:
                decision = "buy"
                reason = f"{tier['name']}_from_entry"
                if not no_trade:
                    order = place_market_order(cfg, "buy", notional=notional)
                    state.setdefault("dip_buys", []).append(tier["name"])
            else:
                reason = "insufficient_cash_for_dip_buy"
    elif should_buy_initial(price, chand_stop, position):
        notional = notional_for(cash, 1.0)
        if notional >= 1:
            decision = "buy"
            reason = "price_above_chandelier_stop"
            if not no_trade:
                order = place_market_order(cfg, "buy", notional=notional)
        else:
            reason = "insufficient_cash_for_initial_buy"

    if order:
        state["last_order_id"] = order.get("id")

    report_written = maybe_write_report(state, account, position)
    save_state(state)

    record = {
        "timestamp": utc_now(),
        "symbol": SYMBOL,
        "mode": "paper",
        "decision": decision,
        "reason": reason,
        "price": price,
        "atr": atr,
        "chandelier_stop": chand_stop,
        "stop_floor": stop_floor,
        "cash": cash,
        "daily_risk": daily_risk,
        "qty": qty,
        "avg_entry": entry,
        "open_orders": len(open_orders),
        "notes": notes,
        "order": order,
        "report_written": report_written,
        "no_trade": no_trade,
    }
    append_jsonl(DECISIONS_LOG, record)
    return record

    summary = (
        f"📊 BTC Bot\n"
        f"Decision: {decision}\n"
        f"Price: {price:.2f}\n"
        f"Cash: {cash:.2f}\n"
        f"Qty: {qty}\n"
        f"Reason: {reason}"
    )

    send_telegram(summary)

def send_telegram(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return  # skip kalau belum diset

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg
        }).encode()

        req = urllib.request.Request(url, data=payload)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one strategy evaluation.")
    parser.add_argument("--no-trade", action="store_true", help="Evaluate without submitting orders.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between runs (loop mode).")
    args = parser.parse_args()

    if not (args.once or args.loop):
        parser.error("Use --once or --loop")

    def run_safe():
        try:
            record = run_once(no_trade=args.no_trade)
            print(json.dumps(record, indent=2, sort_keys=True))
        except Exception as exc:
            error_record = {"timestamp": utc_now(), "error": str(exc)}
            append_jsonl(DECISIONS_LOG, error_record)
            print(json.dumps(error_record, indent=2, sort_keys=True))

    if args.once:
        run_safe()
        return 0

    # LOOP MODE
    while True:
        run_safe()
        time.sleep(max(10, args.interval))

    except Exception as exc:
    error_record = {"timestamp": utc_now(), "error": str(exc)}
    append_jsonl(DECISIONS_LOG, error_record)

    send_telegram(f"❌ ERROR:\n{str(exc)}")

    print(json.dumps(error_record, indent=2, sort_keys=True))
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
