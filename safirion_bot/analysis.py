"""Motor de análise: tendência, regime, setups e score 0-100.

O score de cada direção soma componentes com pesos fixos (total 100):

    Tendência por EMAs ........ 25
    Força (ADX/DI) ............ 15
    Momento (MACD) ............ 15
    RSI ....................... 15
    Bollinger ................. 10
    Suporte/Resistência ....... 10
    Padrão de candle .......... 10

Além do setup de tendência, é calculado um setup de reversão (preço em
suporte/resistência + RSI extremo + candle de reversão + banda de Bollinger).
O maior score entre as direções/setups define o sinal; abaixo do limiar,
o resultado é ``NONE`` (sem operação).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .patterns import Levels, candle_patterns, support_resistance

MIN_CANDLES = 250


@dataclass
class StrategyConfig:
    min_score: int = 70            # score mínimo para emitir sinal
    min_margin: int = 15           # diferença mínima entre direções
    modality: str = "digital"      # "digital" (expiração fixa) ou "forex" (SL/TP)
    expiry_candles: int = 3        # expiração em candles (modo digital)
    sl_atr: float = 1.5            # stop em ATRs (modo forex)
    tp_atr: float = 2.0            # alvo em ATRs (modo forex)
    min_atr_pct: float = 0.0002    # mercado "morto" abaixo disso (ATR/preço)


@dataclass
class AnalysisResult:
    symbol: str
    timeframe: str
    time: str
    price: float
    signal: str                    # BUY | SELL | NONE
    score: int                     # score da direção escolhida (0-100)
    score_buy: int
    score_sell: int
    setup: str                     # tendência | rompimento | reversão | -
    trend: str                     # alta | baixa | lateral
    regime: str                    # tendência | consolidação
    modality: str
    expiry_candles: int | None
    stop_loss: float | None
    take_profit: float | None
    indicators: dict
    patterns: list
    support: float | None
    resistance: float | None
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _f(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 6)


def _trend(row) -> str:
    if row.ema50 > row.ema200 and row.close > row.ema50 and row.ema9 > row.ema21:
        return "alta"
    if row.ema50 < row.ema200 and row.close < row.ema50 and row.ema9 < row.ema21:
        return "baixa"
    return "lateral"


def _is_consolidation(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    widths = df["bb_width"].tail(100).dropna()
    squeeze = len(widths) >= 50 and last.bb_width <= widths.quantile(0.2)
    return bool(last.adx < 20 or squeeze)


def _trend_score(df: pd.DataFrame, levels: Levels, pats, d: int) -> tuple[int, list[str], bool]:
    """Score do setup a favor da tendência para a direção ``d`` (+1 compra, -1 venda)."""
    r, prev = df.iloc[-1], df.iloc[-2]
    s, why = 0, []

    def gt(a, b):
        return a > b if d > 0 else a < b

    # EMAs (25)
    ema_pts = sum(5 for a, b in [(r.close, r.ema200), (r.ema50, r.ema200), (r.ema21, r.ema50),
                                 (r.ema9, r.ema21), (r.close, r.ema9)] if gt(a, b))
    s += ema_pts
    if ema_pts >= 20:
        why.append(f"EMAs alinhadas ({ema_pts}/25)")

    # ADX (15)
    di_ok = gt(r.plus_di, r.minus_di)
    if di_ok and r.adx >= 25:
        s += 15; why.append(f"ADX forte {r.adx:.0f}")
    elif di_ok and r.adx >= 20:
        s += 8

    # MACD (15)
    if gt(r.macd_hist, 0):
        s += 7
        if gt(r.macd_hist, prev.macd_hist):
            s += 8; why.append("MACD acelerando")

    # RSI (15) — zona de momento saudável, evita extremos
    rsi = r.rsi if d > 0 else 100 - r.rsi
    if 50 < rsi < 70:
        s += 15; why.append(f"RSI {r.rsi:.0f} em zona de momento")
    elif 45 <= rsi <= 50:
        s += 7
    elif rsi >= 75:
        why.append("RSI esticado (penalizado)")

    # Bollinger (10) e rompimento
    breakout = False
    widths = df["bb_width"].tail(100).dropna()
    recent_squeeze = len(widths) >= 50 and df["bb_width"].iloc[-6:-1].min() <= widths.quantile(0.2)
    pctb = r.bb_pctb if d > 0 else 1 - r.bb_pctb
    if pctb > 1.0 and recent_squeeze:
        s += 10; breakout = True; why.append("rompimento da banda após compressão")
    elif 0.5 <= pctb <= 0.95:
        s += 6

    # Suporte/Resistência (10)
    atr = r.atr
    if d > 0:
        obstacle = levels.nearest_resistance(r.close)
        broke = any(prev.close <= lv < r.close for lv in levels.resistances)
    else:
        obstacle = levels.nearest_support(r.close)
        broke = any(prev.close >= lv > r.close for lv in levels.supports)
    if broke:
        s += 10; breakout = True; why.append("rompeu nível de S/R")
    elif obstacle is None or abs(obstacle - r.close) > atr:
        s += 10
    elif abs(obstacle - r.close) < 0.5 * atr:
        why.append("nível de S/R muito próximo (penalizado)")
    else:
        s += 5

    # Padrões (10)
    if any(pd_ == d for _, pd_ in pats):
        s += 10; why.append("padrão de candle a favor")
    elif any(pd_ == -d for _, pd_ in pats):
        s -= 10; why.append("padrão de candle contra")

    return max(0, min(100, s)), why, breakout


def _reversal_score(df: pd.DataFrame, levels: Levels, pats, d: int) -> tuple[int, list[str]]:
    """Setup de reversão para ``d``: compra em suporte / venda em resistência."""
    r = df.iloc[-1]
    s, why = 0, []
    atr = r.atr
    lvl = levels.nearest_support(r.close) if d > 0 else levels.nearest_resistance(r.close)
    extreme = r.low if d > 0 else r.high
    if lvl is not None and abs(extreme - lvl) <= 0.5 * atr:
        s += 30; why.append(f"tocou {'suporte' if d > 0 else 'resistência'} {lvl:.5g}")
    rsi = r.rsi if d > 0 else 100 - r.rsi
    if rsi <= 30:
        s += 25; why.append(f"RSI extremo {r.rsi:.0f}")
    elif rsi <= 38:
        s += 12
    if any(pd_ == d for _, pd_ in pats):
        s += 25; why.append("candle de reversão")
    pctb = r.bb_pctb if d > 0 else 1 - r.bb_pctb
    if pctb <= 0.05:
        s += 20; why.append("fora/na banda de Bollinger")
    # reversão contra tendência forte é arriscada
    if r.adx >= 35 and ((d > 0 and r.minus_di > r.plus_di) or (d < 0 and r.plus_di > r.minus_di)):
        s -= 20; why.append("tendência contrária forte (penalizado)")
    return max(0, min(100, s)), why


def analyze(df: pd.DataFrame, symbol: str, timeframe: str, cfg: StrategyConfig | None = None) -> AnalysisResult:
    """Analisa o último candle FECHADO de ``df`` (o chamador remove o candle em formação)."""
    cfg = cfg or StrategyConfig()
    if len(df) < MIN_CANDLES:
        raise ValueError(f"são necessários pelo menos {MIN_CANDLES} candles (recebidos {len(df)})")
    data = ind.add_all(df)
    r = data.iloc[-1]
    levels = support_resistance(data)
    pats = candle_patterns(data)
    trend = _trend(r)
    consolidation = _is_consolidation(data)

    cands = []  # (score, direção, setup, motivos)
    for d in (+1, -1):
        ts, twhy, breakout = _trend_score(data, levels, pats, d)
        if consolidation and not breakout:
            ts = int(ts * 0.6); twhy.append("consolidação sem rompimento (penalizado)")
        cands.append((ts, d, "rompimento" if breakout else "tendência", twhy))
        rs, rwhy = _reversal_score(data, levels, pats, d)
        cands.append((rs, d, "reversão", rwhy))

    best_buy = max((c for c in cands if c[1] > 0), key=lambda c: c[0])
    best_sell = max((c for c in cands if c[1] < 0), key=lambda c: c[0])
    best = max(best_buy, best_sell, key=lambda c: c[0])
    other = best_sell if best is best_buy else best_buy

    reasons = list(best[3])
    signal = "BUY" if best[1] > 0 else "SELL"
    atr_pct = r.atr / r.close
    if atr_pct < cfg.min_atr_pct:
        signal = "NONE"; reasons.append(f"volatilidade muito baixa (ATR {atr_pct:.4%})")
    elif best[0] < cfg.min_score:
        signal = "NONE"; reasons.append(f"score {best[0]} abaixo do mínimo {cfg.min_score}")
    elif best[0] - other[0] < cfg.min_margin:
        signal = "NONE"; reasons.append("direções sem vantagem clara")

    sl = tp = None
    if signal != "NONE" and cfg.modality == "forex":
        d = 1 if signal == "BUY" else -1
        sl = _f(r.close - d * cfg.sl_atr * r.atr)
        tp = _f(r.close + d * cfg.tp_atr * r.atr)

    ind_snapshot = {k: _f(r[k]) for k in ["ema9", "ema21", "ema50", "ema200", "rsi", "macd", "macd_signal",
                                         "macd_hist", "bb_upper", "bb_mid", "bb_lower", "bb_width", "atr",
                                         "adx", "plus_di", "minus_di"]}
    return AnalysisResult(
        symbol=symbol, timeframe=timeframe, time=str(data.index[-1]), price=_f(r.close),
        signal=signal, score=int(best[0]), score_buy=int(best_buy[0]), score_sell=int(best_sell[0]),
        setup=best[2] if signal != "NONE" else "-", trend=trend,
        regime="consolidação" if consolidation else "tendência", modality=cfg.modality,
        expiry_candles=cfg.expiry_candles if cfg.modality == "digital" else None,
        stop_loss=sl, take_profit=tp, indicators=ind_snapshot, patterns=[p for p, _ in pats],
        support=_f(levels.nearest_support(r.close)), resistance=_f(levels.nearest_resistance(r.close)),
        reasons=reasons,
    )
