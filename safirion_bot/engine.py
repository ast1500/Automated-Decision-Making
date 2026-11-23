"""Ciclo do robô em modo SINAIS.

A cada execução:
1. busca candles fechados (fonte pública);
2. liquida, com dados públicos, operações abertas (resultado *simulado*);
3. analisa o último candle fechado e grava a análise;
4. se houver sinal e o gerenciamento de risco permitir, registra a operação
   sugerida — para ser executada MANUALMENTE por você na conta DEMO;
5. atualiza heartbeat e painel.

Nenhuma ordem é enviada à Safirion: não há integração autorizada.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import data as market_data
from .analysis import analyze
from .config import BotConfig
from .db import DB, now_iso
from .risk import DayState, RiskManager
from .settlement import settle_digital, settle_forex

log = logging.getLogger("safirion_bot")


def day_start_utc(tz: str, now: datetime | None = None) -> str:
    z = ZoneInfo(tz)
    local = (now or datetime.now(z)).astimezone(z)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")


def day_state(db: DB, cfg: BotConfig) -> DayState:
    start = day_start_utc(cfg.timezone)
    trades = db.trades_since(start)
    balance = db.account()["balance"]
    st = DayState(day=start[:10], start_balance=trades[0]["balance_before"] if trades else balance)
    for t in trades:
        if t["status"] == "OPEN":
            continue
        RiskManager.register_result(st, t["pnl"] or 0.0)
    st.open_trades = len(db.open_trades())
    return st


def settle_open(db: DB, cfg: BotConfig, symbol: str, timeframe: str, candles: pd.DataFrame) -> list[int]:
    closed = []
    for t in db.open_trades(symbol, timeframe):
        ct = pd.Timestamp(t["candle_time"])
        if t["modality"] == "digital":
            s = settle_digital(candles, ct, t["direction"], t["entry_price"], t["stake"],
                               t["expiry_candles"], cfg.payout, timeframe)
        else:
            s = settle_forex(candles, ct, t["direction"], t["entry_price"], t["stake"],
                             t["stop_loss"], t["take_profit"], cfg.max_hold_candles, timeframe)
        if s is None:
            continue
        db.update_trade(t["id"], status=s.status, exit_price=s.exit_price, closed_at=s.exit_time.isoformat(),
                        sim_pnl=s.pnl, pnl=s.pnl)
        db.add_to_balance(s.pnl)
        closed.append(t["id"])
        log.info("operação #%s liquidada (simulada): %s %+.2f", t["id"], s.status, s.pnl)
    return closed


def record_demo_result(db: DB, trade_id: int, result: str, pnl: float, screenshot: str | None = None,
                       note: str | None = None) -> None:
    """Registra o resultado REAL obtido por você na conta DEMO da Safirion.

    Substitui o resultado simulado e ajusta o saldo pela diferença."""
    result = result.upper()
    if result not in ("WIN", "LOSS", "DRAW"):
        raise ValueError("resultado deve ser WIN, LOSS ou DRAW")
    t = db.trade(trade_id)
    if t is None:
        raise ValueError(f"operação #{trade_id} não existe")
    previous = t["pnl"] or 0.0
    fields = dict(status=result, demo_result=result, demo_pnl=round(pnl, 2), pnl=round(pnl, 2))
    if t["status"] == "OPEN":
        fields["closed_at"] = now_iso()
    if screenshot:
        fields["screenshot"] = screenshot
    if note:
        fields["note"] = note
    db.update_trade(trade_id, **fields)
    db.add_to_balance(round(pnl - previous, 2))


def run_cycle(db: DB, cfg: BotConfig, fetcher=None) -> list[dict]:
    """Executa um ciclo completo. ``fetcher(symbol, timeframe)`` permite injetar dados em testes."""
    fetcher = fetcher or (lambda s, tf: market_data.fetch(cfg.source, s, tf))
    db.ensure_account(cfg.initial_demo_balance)
    rm = RiskManager(cfg.risk)
    report, errors = [], []
    for m in cfg.markets:
        try:
            candles = fetcher(m.symbol, m.timeframe)
            settle_open(db, cfg, m.symbol, m.timeframe, candles)
            res = analyze(candles, m.symbol, m.timeframe, cfg.strategy)
            aid = db.save_analysis(res)
            entry = {"market": f"{m.symbol} {m.timeframe}", "signal": res.signal, "score": res.score,
                     "new_candle": aid != -1, "trade": None, "risk": None}
            if res.signal != "NONE" and aid != -1:
                tf = pd.Timedelta(minutes=market_data.TIMEFRAMES[m.timeframe])
                age = pd.Timestamp.now(tz="UTC") - (pd.Timestamp(res.time) + tf)
                state = day_state(db, cfg)
                balance = db.account()["balance"]
                decision = rm.check(state, balance)
                entry["risk"] = decision.reason
                if age > tf:
                    entry["risk"] = f"sinal expirado (atraso de {age})"
                elif decision.allowed:
                    tid = db.open_trade(
                        analysis_id=aid, opened_at=now_iso(), candle_time=res.time, symbol=m.symbol,
                        timeframe=m.timeframe, direction=res.signal, modality=res.modality,
                        stake=decision.stake, balance_before=balance, entry_price=res.price,
                        expiry_candles=res.expiry_candles, stop_loss=res.stop_loss,
                        take_profit=res.take_profit, score=res.score)
                    entry["trade"] = tid
                    log.info("SINAL %s %s %s score=%s stake=%.2f (#%s)", res.signal, m.symbol, m.timeframe,
                             res.score, decision.stake, tid)
            report.append(entry)
        except Exception as e:  # noqa: BLE001 — um mercado com falha não derruba os outros
            log.exception("falha em %s %s", m.symbol, m.timeframe)
            errors.append(f"{m.symbol} {m.timeframe}: {e}")
    db.beat("engine", {"markets": len(cfg.markets), "errors": errors, "source": cfg.source})
    return report
