# BTC/USD Chandelier Stop Paper Bot

This workspace runs a paper-only Alpaca BTC/USD Chandelier Stop strategy.

## Risk Rules

- Alpaca endpoint must be `https://paper-api.alpaca.markets`.
- Initial max buy size is 10% of available cash.
- Max daily loss is 50% of the day's starting cash.
- Initial stop floor is 5% below average entry, combined with the Chandelier Stop.
- After price is at least 10% above average entry, the stop floor can rise to 5% below current price.
- The stop floor only moves up while a position exists.
- If holding BTC/USD and price drops 20% below average entry, buy 2x max size once.
- If holding BTC/USD and price drops 30% below average entry, buy 3x max size once.
- If the daily loss limit is reached, the bot stops opening or adding positions until the next Jakarta trading day; risk-reducing stop sells are still allowed.

## Run

```powershell
.\run_btcusd.ps1
```

Use `.\run_btcusd.ps1 --no-trade` to evaluate without submitting paper orders.

Logs are written to `logs/decisions.jsonl`; 24-hour summaries are appended to `logs/reports.md`.

## Schedule

Codex heartbeat mode can block outbound network sockets. To run reliably every
5 minutes, install the local Windows scheduled task:

```powershell
.\install_btcusd_task.ps1
```

The scheduled wrapper appends execution details to `logs/scheduler.log`.

To run even when the Windows user is logged off, install it with a credential
prompt:

```powershell
.\install_btcusd_task.ps1 -RunWhetherLoggedOnOrNot
```
