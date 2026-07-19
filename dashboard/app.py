"""FastAPI dashboard.

Server-rendered read-only views over the bot database. Shares the SQLite file
with the bot process rather than talking to it directly, so the dashboard can
restart independently.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config.settings import load_config
from storage.db import Database

config = load_config()
db = Database(config.db_path)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="PolyEdge Dashboard", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    portfolio = await db.get_portfolio_value(config.trading.initial_balance)
    today_pnl = await db.get_today_pnl()
    open_trades = await db.get_open_trades()
    daily_stats = await db.get_daily_stats(7)

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "portfolio": portfolio,
            "initial_balance": config.trading.initial_balance,
            "today_pnl": today_pnl,
            "open_trades": open_trades,
            "daily_stats": daily_stats,
            "paper_mode": config.trading.paper_trading,
        },
    )


@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request) -> HTMLResponse:
    signals = await db.get_recent_signals(50)
    for s in signals:
        if s.get("key_factors"):
            try:
                s["key_factors_list"] = json.loads(s["key_factors"])
            except (json.JSONDecodeError, TypeError):
                s["key_factors_list"] = []
        else:
            s["key_factors_list"] = []

    return templates.TemplateResponse(
        request,
        "signals.html",
        context={"signals": signals},
    )


@app.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request) -> HTMLResponse:
    trades = await db.get_recent_trades(50)
    return templates.TemplateResponse(
        request,
        "trades.html",
        context={"trades": trades},
    )


@app.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request) -> HTMLResponse:
    today_cost = await db.get_today_api_cost()
    daily_stats = await db.get_daily_stats(30)

    return templates.TemplateResponse(
        request,
        "costs.html",
        context={
            "today_cost": today_cost,
            "daily_stats": daily_stats,
        },
    )
