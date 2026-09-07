<img src="docs/hero.png" alt="polyedge — cheap pass first" width="100%"/>

<p><img src="https://github.com/AdrianAdem/polyedge/actions/workflows/ci.yml/badge.svg" alt="" height="20"/> <img src="https://img.shields.io/badge/license-MIT-0C1030?style=flat-square" alt="" height="20"/> <img src="https://img.shields.io/badge/python-3.11+-0C1030?style=flat-square" alt="" height="20"/> <img src="https://img.shields.io/badge/execution-paper%20only-6C8BFF?style=flat-square" alt="" height="20"/></p>

**Contents** &nbsp;·&nbsp; [The problem](#the-problem) &nbsp;·&nbsp; [Features](#features) &nbsp;·&nbsp; [Screenshots](#screenshots) &nbsp;·&nbsp; [Tech stack](#tech-stack) &nbsp;·&nbsp; [Architecture](#architecture) &nbsp;·&nbsp; [Installation](#installation) &nbsp;·&nbsp; [Usage](#usage) &nbsp;·&nbsp; [Project layout](#project-layout) &nbsp;·&nbsp; [Roadmap](#roadmap) &nbsp;·&nbsp; [Disclaimer](#disclaimer) &nbsp;·&nbsp; [License](#license)

Event-driven signal scanner for Polymarket prediction markets, using a two-tier LLM pipeline to find mispriced contracts and alert via Telegram.

## The problem

Prediction markets price events continuously, but the news that moves those prices arrives faster than most participants re-evaluate their positions. A market on "Will the Fed cut rates in June?" may sit at $0.42 for hours after a CPI print that should have moved it. Spotting that gap manually means watching hundreds of markets and several news feeds at once.

PolyEdge automates the watching. It scans open Polymarket contracts, correlates them with live news, macro releases and crypto price action, and asks an LLM to estimate the true probability. When that estimate diverges far enough from the market price — with enough confidence, and in a market liquid enough to trade — it sizes the position and pushes an alert.

The binding constraint is cost. A naive implementation calls an LLM once per market per scan, which at ~1,700 open markets on a 5-minute interval runs to hundreds of dollars a day. The two-tier design exists to solve that, and holds the running cost to roughly $3–8/day.

## Features

- **Two-tier LLM pipeline** — a cheap Haiku pass filters every candidate for relevance; only markets flagged relevant *and* urgent reach the expensive Sonnet analysis. Prompt caching is enabled on both system prompts.
- **Multi-source context** — Polymarket prices, Binance trade streams (30-minute rolling momentum and realised volatility), RSS news with fuzzy deduplication, and FRED macro series (CPI, Fed funds, unemployment, GDP).
- **Quarter-Kelly position sizing** — capped at 0.5% of portfolio per trade, with daily exposure limits, a hard stop at 0.4% daily loss, a maximum of 5 concurrent positions, and rejection of illiquid (>5% spread) or near-expiry markets.
- **Paper trading by default** — simulated fills at live prices, settled against real market resolutions. Live execution is a deliberate stub that raises `NotImplementedError`.
- **Cost accounting** — every API call logs tokens, latency and USD cost to SQLite, queryable per day and per trade.
- **Telegram control** — signal alerts plus `/status`, `/history`, `/pause`, `/resume`, `/costs`.
- **Server-rendered dashboard** — FastAPI and Jinja2, no frontend framework: portfolio, signals, trade history, cost tracking.
- **Fails closed** — malformed LLM responses and API errors resolve to "no signal", never to a trade on bad data.

## Screenshots

> Screenshots show paper-trading demo data, not a live track record.

**Portfolio overview** — value, daily PnL, open positions and a seven-day breakdown.

![Dashboard overview](docs/screenshots/dashboard-overview.png)

**Signal feed** — each verdict with edge, confidence, the model's probability estimate, its reasoning and key factors. Markets the Tier 1 filter rejected appear without a verdict, so the filtering itself stays auditable.

![Signal feed](docs/screenshots/signals.png)

**Trade history** — entry, exit and settled PnL per position.

![Trade history](docs/screenshots/trades.png)

**API cost tracking** — spend per day and per trade, the metric the two-tier design exists to control.

![API cost tracking](docs/screenshots/costs.png)

**Telegram alert** — the format pushed on every approved signal:

```
🎯 SIGNAL: BUY_YES
Market: "Will the Fed cut rates at the June 2026 meeting?"
Current Price: $0.42 (42%)
Edge: 16.0%
Confidence: 0.82
Score: 0.131
Suggested Size: $13.50
Key Factors:
  - CPI 2.1% vs 2.4% consensus
  - Two Fed governors dovish
  - Bond market implies 65%

Reasoning: CPI printed 2.1% against 2.4% consensus, and two governors
shifted dovish this week. Bond futures moved before this market did.
```

## Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Runtime | Python 3.11+, `asyncio` | Concurrent I/O across four feeds without threads |
| LLM | Anthropic SDK (Haiku + Sonnet) | Tiered cost control, prompt caching, built-in retry |
| HTTP | `httpx` | Async client with timeouts and connection reuse |
| Streaming | `websockets` | Persistent Binance trade stream |
| Storage | SQLite via `aiosqlite` | Single file, no daemon; async so writes never block scanning |
| Config | `pydantic` + `python-dotenv` | Typed settings, environment-only secrets |
| Dashboard | FastAPI + Jinja2 + Tailwind (CDN) | Server-rendered, no build step |
| Logging | `structlog` | Structured output, greppable by event name |
| Tooling | `ruff`, `pytest`, GitHub Actions | Lint, format and test on 3.11 and 3.12 |

## Architecture

```mermaid
flowchart TD
    subgraph Feeds["Data feeds (background tasks)"]
        PM[Polymarket<br/>Gamma API]
        BN[Binance<br/>WebSocket]
        NW[RSS feeds<br/>news]
        FR[FRED<br/>macro]
    end

    subgraph Analysis["Analysis"]
        SC[Scanner<br/>volume + expiry filter]
        T1[Tier 1: Haiku<br/>relevance filter]
        T2[Tier 2: Sonnet<br/>probability estimate]
        SE[Signal engine<br/>edge x confidence]
    end

    subgraph Control["Risk + execution"]
        RM[Risk manager<br/>Kelly + limits]
        PT[Paper trader]
        LV[Live trader<br/>disabled]
    end

    DB[(SQLite)]
    TG[Telegram]
    DASH[FastAPI dashboard]

    PM --> SC
    SC -->|top N by volume| T1
    NW --> T1
    T1 -->|relevant AND urgency >= 3| T2
    BN --> T2
    FR --> T2
    NW --> T2
    T2 -->|BUY_YES / BUY_NO| SE
    SE --> RM
    RM -->|sized| PT
    RM -.->|phase 2| LV
    PT --> DB
    T1 --> DB
    T2 --> DB
    RM --> TG
    DB --> DASH
```

The scan loop runs every 5 minutes. Feeds run as independent background tasks — if the Binance socket drops or a feed returns 500s, context quality degrades but the loop continues. Open paper positions are settled each cycle against resolved markets.

## Installation

Requires Python 3.11 or newer.

```bash
git clone https://github.com/AdrianAdem/polyedge.git
cd polyedge

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in your keys
```

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Both analysis tiers |
| `TELEGRAM_BOT_TOKEN` | no | — | Alerts; omit to run silently |
| `TELEGRAM_CHAT_ID` | no | — | Alert destination |
| `FRED_API_KEY` | no | — | Macro context; omit to run without it |
| `PAPER_TRADING` | no | `true` | Live execution stays disabled regardless |
| `INITIAL_BALANCE` | no | `500` | Starting paper portfolio, USD |
| `MAX_RISK_PER_TRADE` | no | `0.005` | Cap per position, as portfolio fraction |
| `MAX_DAILY_RISK` | no | `0.02` | Daily exposure cap |
| `HARD_STOP_LOSS` | no | `0.004` | Daily loss that halts all new signals |
| `SCAN_INTERVAL_SECONDS` | no | `300` | Seconds between scans |
| `MIN_MARKET_VOLUME` | no | `50000` | Volume floor, USD |
| `MIN_EDGE_THRESHOLD` | no | `0.10` | Minimum edge for a signal |
| `MIN_CONFIDENCE_THRESHOLD` | no | `0.70` | Minimum model confidence |
| `MAX_MARKETS_PER_SCAN` | no | `40` | Cost cap: one Haiku call per market |
| `DB_PATH` | no | `polyedge.db` | SQLite location |

Polymarket credentials appear in `.env.example` but are unused: market data comes from the public Gamma API, and no wallet is needed while live trading is disabled.

## Usage

```bash
# Run the bot
python main.py

# Dashboard, separate process, http://localhost:8000
uvicorn dashboard.app:app --port 8000

# Both, containerised
docker compose up -d

# Tests and linting
pip install -r requirements-dev.txt
pytest -q
ruff check .
ruff format .
```

Expected startup output:

```
polyedge_starting            paper_mode=True
binance_feed_started         symbols=['btcusdt', 'ethusdt']
news_aggregator_started      feeds=6
fred_data_updated            cpi=332.407 fed_rate=3.64 unemployment=4.3
polyedge_main_loop_started
scan_cycle_complete          markets_scanned=40 next_scan_seconds=300
```

Telegram commands: `/status` (portfolio and open positions), `/history` (recent trades), `/pause` and `/resume` (halt scanning), `/costs` (today's API spend).

## Project layout

```
polyedge/
├── config/       settings (env-driven) and market categorisation
├── data/         Polymarket, Binance, RSS news, FRED clients
├── analysis/     scanner, two-tier LLM analyst, signal engine
├── risk/         Kelly sizing and risk limits
├── execution/    paper trader, Telegram bot, live stub
├── storage/      SQLite layer and domain models
├── dashboard/    FastAPI app and Jinja2 templates
├── tests/        unit tests for sizing, categorisation, gating, risk
└── main.py       orchestrator
```

## Roadmap

- [x] Phase 1 — data pipeline, two-tier analysis, risk management, paper trading
- [ ] Phase 2 — live execution via the CLOB API, gated on two or more weeks of profitable paper trading
- [ ] Backtesting against historical market resolutions
- [ ] Calibration tracking: predicted probability versus realised outcome frequency

## Disclaimer

**This is a learning and research project. It is not investment advice.**

- Paper trading only. Live execution is not implemented — `execution/live.py` raises `NotImplementedError` by design.
- Nothing here has been validated against real capital, and no claim is made that the strategy is profitable. LLM probability estimates are not calibrated forecasts.
- Prediction markets are legally restricted in many jurisdictions, including for US persons on some venues. Check your local regulations before trading.
- If you adapt this for live trading, you do so entirely at your own risk. The author accepts no liability for financial losses.

## License

MIT — see [LICENSE](LICENSE).
