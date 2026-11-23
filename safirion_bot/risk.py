"""Gerenciamento de risco (configuração experimental, sem Martingale/Gale).

Regras:
* valor fixo de ``stake_pct`` (1%) do saldo atual por operação — nunca
  aumenta após perda (sem Martingale/Gale);
* no máximo ``max_trades_per_day`` (10) operações por dia;
* para no dia após ``max_consecutive_losses`` (3) perdas seguidas;
* para no dia ao atingir ``daily_loss_limit_pct`` (5%) de perda;
* para no dia ao atingir ``daily_target_pct`` (3%) de ganho;
* uma operação aberta por vez.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    stake_pct: float = 0.01
    max_trades_per_day: int = 10
    max_consecutive_losses: int = 3
    daily_loss_limit_pct: float = 0.05
    daily_target_pct: float = 0.03
    min_stake: float = 1.0          # valor mínimo aceito pela plataforma (ajuste conforme a Safirion)
    allow_martingale: bool = False  # mantido apenas para deixar explícito; não é usado


@dataclass
class DayState:
    day: str
    start_balance: float
    trades: int = 0
    consecutive_losses: int = 0
    pnl: float = 0.0
    open_trades: int = 0


@dataclass
class RiskDecision:
    allowed: bool
    stake: float
    reason: str


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None):
        self.cfg = cfg or RiskConfig()
        if self.cfg.allow_martingale:
            raise ValueError("Martingale/Gale não é permitido nesta configuração")

    def stake_for(self, balance: float) -> float:
        return round(max(self.cfg.min_stake, balance * self.cfg.stake_pct), 2)

    def check(self, state: DayState, balance: float) -> RiskDecision:
        c = self.cfg
        if state.open_trades > 0:
            return RiskDecision(False, 0.0, "já existe operação aberta")
        if state.trades >= c.max_trades_per_day:
            return RiskDecision(False, 0.0, f"limite diário de {c.max_trades_per_day} operações atingido")
        if state.consecutive_losses >= c.max_consecutive_losses:
            return RiskDecision(False, 0.0, f"{state.consecutive_losses} perdas consecutivas — pausa até amanhã")
        if state.pnl <= -c.daily_loss_limit_pct * state.start_balance:
            return RiskDecision(False, 0.0, f"limite de perda diária ({c.daily_loss_limit_pct:.0%}) atingido")
        if state.pnl >= c.daily_target_pct * state.start_balance:
            return RiskDecision(False, 0.0, f"meta diária ({c.daily_target_pct:.0%}) atingida")
        stake = self.stake_for(balance)
        if stake > balance:
            return RiskDecision(False, 0.0, "saldo insuficiente")
        # Uma perda total desta operação não pode ultrapassar o limite diário.
        remaining = c.daily_loss_limit_pct * state.start_balance + state.pnl
        if stake > remaining + 1e-9:
            return RiskDecision(False, 0.0, "operação ultrapassaria o limite de perda diária")
        return RiskDecision(True, stake, "ok")

    @staticmethod
    def register_result(state: DayState, pnl: float) -> DayState:
        state.trades += 1
        state.pnl = round(state.pnl + pnl, 2)
        if pnl < 0:
            state.consecutive_losses += 1
        elif pnl > 0:
            state.consecutive_losses = 0
        return state
