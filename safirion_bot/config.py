"""Carrega ``config.json`` (sem segredos — o robô não usa nem pede senha)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import StrategyConfig
from .risk import RiskConfig

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Market:
    symbol: str
    timeframe: str


@dataclass
class BotConfig:
    source: str = "kraken"
    markets: list[Market] = field(default_factory=lambda: [Market("BTCUSD", "M15")])
    timezone: str = "America/Sao_Paulo"
    initial_demo_balance: float = 10000.0
    payout: float = 0.85               # retorno da opção digital; confira o valor exibido na Safirion
    max_hold_candles: int = 24          # modo forex
    db_path: str = "data/safirion.db"
    dashboard_path: str = "docs/index.html"
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def db_file(self) -> Path:
        return ROOT / self.db_path

    @property
    def dashboard_file(self) -> Path:
        return ROOT / self.dashboard_path


def load(path: str | Path | None = None) -> BotConfig:
    p = Path(path) if path else ROOT / "config.json"
    raw = json.loads(p.read_text()) if p.exists() else {}
    cfg = BotConfig(**{k: v for k, v in raw.items() if k not in ("markets", "strategy", "risk", "_comment")})
    if "markets" in raw:
        cfg.markets = [Market(**m) for m in raw["markets"]]
    cfg.strategy = StrategyConfig(**raw.get("strategy", {}))
    cfg.risk = RiskConfig(**raw.get("risk", {}))
    if not 0 < cfg.payout <= 1.5:
        raise ValueError("payout deve estar entre 0 e 1.5 (ex.: 0.85 = 85%)")
    return cfg
