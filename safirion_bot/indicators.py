"""Indicadores técnicos calculados localmente (sem dependência de plataforma).

Todas as funções recebem Series/DataFrame do pandas com colunas
``open, high, low, close`` e devolvem Series alinhadas ao índice de entrada.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Média suavizada de Wilder (RMA), usada por RSI, ATR e ADX."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0.0), period)
    loss = _wilder(-delta.clip(upper=0.0), period)
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # Sem perdas no período => RSI 100; sem ganhos => 0.
    out = out.where(loss != 0.0, 100.0)
    out = out.where(~((gain == 0.0) & (loss == 0.0)), 50.0)
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper, lower = mid + mult * std, mid - mult * std
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pctb": pct_b}
    )


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = _wilder(true_range(df), period)
    plus_di = 100.0 * _wilder(plus_dm, period) / tr
    minus_di = 100.0 * _wilder(minus_dm, period) / tr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return pd.DataFrame({"adx": _wilder(dx.fillna(0.0), period), "plus_di": plus_di, "minus_di": minus_di})


def add_all(df: pd.DataFrame, ema_periods=(9, 21, 50, 200)) -> pd.DataFrame:
    """Devolve uma cópia de ``df`` com todas as colunas de indicadores."""
    out = df.copy()
    for p in ema_periods:
        out[f"ema{p}"] = ema(out["close"], p)
    out["rsi"] = rsi(out["close"], 14)
    out = out.join(macd(out["close"]))
    out = out.join(bollinger(out["close"]))
    out["atr"] = atr(out, 14)
    out = out.join(adx(out, 14))
    return out
