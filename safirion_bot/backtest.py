"""Backtest candle a candle, com as mesmas regras de análise, risco e liquidação do ciclo ao vivo."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

import pandas as pd

from .analysis import MIN_CANDLES, StrategyConfig, analyze
from .risk import DayState, RiskConfig, RiskManager
from .settlement import settle_digital, settle_forex

WINDOW = 400  # candles passados para o motor a cada passo


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    period: str
    candles: int
    modality: str
    payout: float
    initial_balance: float
    final_balance: float
    trades: int
    wins: int
    losses: int
    draws: int
    win_rate: float
    breakeven_win_rate: float | None
    pnl: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    blocked_by_risk: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    equity: list = field(default_factory=list)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("log"); d.pop("equity")
        return d


def run_backtest(candles: pd.DataFrame, symbol: str, timeframe: str, strategy: StrategyConfig | None = None,
                 risk: RiskConfig | None = None, payout: float = 0.85, initial_balance: float = 10000.0,
                 tz: str = "America/Sao_Paulo", max_hold: int = 24) -> BacktestResult:
    strategy = strategy or StrategyConfig()
    rm = RiskManager(risk or RiskConfig())
    zone = ZoneInfo(tz)
    balance, peak, max_dd = initial_balance, initial_balance, 0.0
    state: DayState | None = None
    open_t: dict | None = None
    log, equity, blocked = [], [], {}
    idx = candles.index

    for i in range(MIN_CANDLES - 1, len(candles)):
        t = idx[i]
        day = t.astimezone(zone).date().isoformat()
        if state is None or state.day != day:
            state = DayState(day=day, start_balance=balance, open_trades=1 if open_t else 0)

        if open_t:
            seen = candles.iloc[: i + 1]
            if open_t["modality"] == "digital":
                s = settle_digital(seen, open_t["t"], open_t["dir"], open_t["entry"], open_t["stake"],
                                   strategy.expiry_candles, payout, timeframe)
            else:
                s = settle_forex(seen, open_t["t"], open_t["dir"], open_t["entry"], open_t["stake"],
                                 open_t["sl"], open_t["tp"], max_hold, timeframe)
            if s is None:
                continue
            balance = round(balance + s.pnl, 2)
            state.open_trades = 0
            RiskManager.register_result(state, s.pnl)
            log.append({**{k: v for k, v in open_t.items() if k != "t"}, "time": str(open_t["t"]),
                        "status": s.status, "exit": s.exit_price, "pnl": s.pnl, "balance": balance})
            equity.append((str(s.exit_time), balance))
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak)
            open_t = None

        res = analyze(candles.iloc[max(0, i + 1 - WINDOW): i + 1], symbol, timeframe, strategy)
        if res.signal == "NONE":
            continue
        decision = rm.check(state, balance)
        if not decision.allowed:
            blocked[decision.reason] = blocked.get(decision.reason, 0) + 1
            continue
        open_t = {"t": t, "dir": res.signal, "entry": res.price, "stake": decision.stake, "score": res.score,
                  "setup": res.setup, "modality": res.modality, "sl": res.stop_loss, "tp": res.take_profit}
        state.open_trades = 1

    wins = sum(1 for x in log if x["status"] == "WIN")
    losses = sum(1 for x in log if x["status"] == "LOSS")
    draws = len(log) - wins - losses
    gross_win = sum(x["pnl"] for x in log if x["pnl"] > 0)
    gross_loss = -sum(x["pnl"] for x in log if x["pnl"] < 0)
    decided = wins + losses
    return BacktestResult(
        symbol=symbol, timeframe=timeframe, period=f"{idx[0]} → {idx[-1]}", candles=len(candles),
        modality=strategy.modality, payout=payout, initial_balance=initial_balance, final_balance=balance,
        trades=len(log), wins=wins, losses=losses, draws=draws,
        win_rate=round(wins / decided, 4) if decided else 0.0,
        breakeven_win_rate=round(1 / (1 + payout), 4) if strategy.modality == "digital" else None,
        pnl=round(balance - initial_balance, 2), return_pct=round((balance / initial_balance - 1) * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss else None,
        blocked_by_risk=blocked, log=log, equity=equity,
    )
