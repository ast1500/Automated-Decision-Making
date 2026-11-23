"""Padrões de candles e níveis de suporte/resistência."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def candle_patterns(df: pd.DataFrame) -> list[tuple[str, int]]:
    """Padrões no último candle fechado.

    Retorna lista de ``(nome, direção)`` onde direção é +1 (altista),
    -1 (baixista) ou 0 (indecisão).
    """
    if len(df) < 3:
        return []
    c, p = df.iloc[-1], df.iloc[-2]
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    rng = h - l
    if rng <= 0:
        return []
    body = abs(cl - o)
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    p_body = abs(p["close"] - p["open"])
    found: list[tuple[str, int]] = []

    if body <= 0.1 * rng:
        found.append(("doji", 0))
    if lower >= 2 * body and upper <= 0.35 * body + 0.1 * rng and body > 0.05 * rng:
        found.append(("martelo", +1))
    if upper >= 2 * body and lower <= 0.35 * body + 0.1 * rng and body > 0.05 * rng:
        found.append(("estrela_cadente", -1))
    p_bear, p_bull = p["close"] < p["open"], p["close"] > p["open"]
    if p_bear and cl > o and o <= p["close"] and cl >= p["open"] and body > p_body:
        found.append(("engolfo_alta", +1))
    if p_bull and cl < o and o >= p["close"] and cl <= p["open"] and body > p_body:
        found.append(("engolfo_baixa", -1))
    if body >= 0.85 * rng:
        found.append(("marubozu_alta" if cl > o else "marubozu_baixa", 1 if cl > o else -1))
    return found


@dataclass
class Levels:
    supports: list[float]
    resistances: list[float]

    def nearest_support(self, price: float) -> float | None:
        below = [s for s in self.supports if s <= price]
        return max(below) if below else None

    def nearest_resistance(self, price: float) -> float | None:
        above = [r for r in self.resistances if r >= price]
        return min(above) if above else None


def support_resistance(df: pd.DataFrame, window: int = 5, lookback: int = 200, tolerance: float = 0.0015) -> Levels:
    """Níveis a partir de pivôs (máximas/mínimas locais) agrupados por proximidade.

    O último ``window`` de candles não forma pivô (ainda não confirmado).
    """
    data = df.tail(lookback)
    highs, lows = data["high"].to_numpy(), data["low"].to_numpy()
    piv_h, piv_l = [], []
    for i in range(window, len(data) - window):
        if highs[i] == highs[i - window : i + window + 1].max():
            piv_h.append(float(highs[i]))
        if lows[i] == lows[i - window : i + window + 1].min():
            piv_l.append(float(lows[i]))

    def cluster(levels: list[float]) -> list[float]:
        out: list[list[float]] = []
        for lv in sorted(levels):
            if out and abs(lv - out[-1][-1]) / lv <= tolerance:
                out[-1].append(lv)
            else:
                out.append([lv])
        return [sum(g) / len(g) for g in out]

    return Levels(supports=cluster(piv_l), resistances=cluster(piv_h))
