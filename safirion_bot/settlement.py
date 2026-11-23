"""Liquidação simulada de operações a partir de candles (usada no ciclo ao vivo e no backtest)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .data import TIMEFRAMES


@dataclass
class Settlement:
    status: str        # WIN | LOSS | DRAW
    exit_price: float
    exit_time: pd.Timestamp
    pnl: float


def settle_digital(candles: pd.DataFrame, candle_time: pd.Timestamp, direction: str, entry: float,
                   stake: float, expiry_candles: int, payout: float, timeframe: str) -> Settlement | None:
    """Opção de expiração fixa: entrada no fechamento do candle do sinal, saída no
    fechamento do ``expiry_candles``-ésimo candle seguinte. ``None`` se ainda não expirou."""
    tf = pd.Timedelta(minutes=TIMEFRAMES[timeframe])
    exit_open = candle_time + expiry_candles * tf
    if exit_open not in candles.index:
        return None
    exit_price = float(candles.at[exit_open, "close"])
    d = 1 if direction == "BUY" else -1
    move = (exit_price - entry) * d
    if move > 0:
        status, pnl = "WIN", stake * payout
    elif move < 0:
        status, pnl = "LOSS", -stake
    else:
        status, pnl = "DRAW", 0.0
    return Settlement(status, exit_price, exit_open + tf, round(pnl, 2))


def settle_forex(candles: pd.DataFrame, candle_time: pd.Timestamp, direction: str, entry: float,
                 stake: float, sl: float, tp: float, max_hold: int, timeframe: str) -> Settlement | None:
    """Posição com stop/alvo. ``stake`` = valor em risco até o stop.

    Se stop e alvo forem tocados no mesmo candle, assume-se o stop (conservador).
    Após ``max_hold`` candles a posição é encerrada no fechamento.
    """
    tf = pd.Timedelta(minutes=TIMEFRAMES[timeframe])
    after = candles[candles.index > candle_time]
    d = 1 if direction == "BUY" else -1
    risk = abs(entry - sl)
    for i, (t, c) in enumerate(after.iterrows(), start=1):
        hit_sl = c.low <= sl if d > 0 else c.high >= sl
        hit_tp = c.high >= tp if d > 0 else c.low <= tp
        if hit_sl:
            return Settlement("LOSS", sl, t + tf, round(-stake, 2))
        if hit_tp:
            return Settlement("WIN", tp, t + tf, round(stake * abs(tp - entry) / risk, 2))
        if i >= max_hold:
            pnl = round(stake * (c.close - entry) * d / risk, 2)
            status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "DRAW"
            return Settlement(status, float(c.close), t + tf, pnl)
    return None
