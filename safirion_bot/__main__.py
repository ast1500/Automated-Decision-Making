"""Linha de comando: ``python -m safirion_bot <comando>``."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config as config_mod
from . import dashboard
from . import data as market_data
from .analysis import analyze
from .backtest import run_backtest
from .config import ROOT
from .db import DB
from .engine import record_demo_result, run_cycle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="safirion_bot", description="Robô de sinais (modo DEMO manual) para a Safirion")
    ap.add_argument("--config", help="caminho do config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="executa um ciclo: dados → liquidação → análise → sinal → painel")

    a = sub.add_parser("analyze", help="analisa um mercado agora, sem gravar")
    a.add_argument("symbol"); a.add_argument("timeframe")

    b = sub.add_parser("backtest", help="backtest com dados públicos")
    b.add_argument("symbol"); b.add_argument("timeframe")
    b.add_argument("--candles", type=int, default=3000)
    b.add_argument("--csv", help="usar arquivo CSV em vez de baixar")
    b.add_argument("--source", choices=["binance", "kraken"], default="binance")

    r = sub.add_parser("record", help="registra o resultado REAL de uma operação feita na conta DEMO")
    r.add_argument("trade_id", type=int)
    r.add_argument("result", choices=["win", "loss", "draw", "WIN", "LOSS", "DRAW"])
    r.add_argument("pnl", type=float, help="lucro/prejuízo em moeda (ex.: 85 ou -100)")
    r.add_argument("--screenshot", help="caminho/URL do print da operação")
    r.add_argument("--note")

    s = sub.add_parser("set-balance", help="sincroniza o saldo com o exibido na conta DEMO")
    s.add_argument("balance", type=float)

    sub.add_parser("confirm-demo", help="declara que as operações são feitas apenas na conta DEMO")
    sub.add_parser("dashboard", help="regera o painel HTML")
    sub.add_parser("status", help="mostra o estado atual em JSON")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_mod.load(args.config)

    if args.cmd == "analyze":
        df = market_data.fetch(cfg.source, args.symbol, args.timeframe)
        print(json.dumps(analyze(df, args.symbol, args.timeframe, cfg.strategy).to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "backtest":
        if args.csv:
            df = market_data.load_csv(args.csv)
        elif args.source == "binance":
            df = market_data.fetch_binance_history(args.symbol, args.timeframe, args.candles)
        else:
            df = market_data.fetch_kraken(args.symbol, args.timeframe)
        res = run_backtest(df, args.symbol, args.timeframe, cfg.strategy, cfg.risk, cfg.payout,
                           cfg.initial_demo_balance, cfg.timezone, cfg.max_hold_candles)
        out = ROOT / "data" / f"backtest_{args.symbol}_{args.timeframe}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({**res.summary(), "log": res.log}, indent=2, ensure_ascii=False, default=str))
        print(json.dumps(res.summary(), indent=2, ensure_ascii=False))
        print(f"salvo em {out.relative_to(ROOT)}")
        return 0

    db = DB(cfg.db_file)
    db.ensure_account(cfg.initial_demo_balance)
    try:
        if args.cmd == "run":
            report = run_cycle(db, cfg)
            print(json.dumps(report, indent=2, ensure_ascii=False))
        elif args.cmd == "record":
            record_demo_result(db, args.trade_id, args.result, args.pnl, args.screenshot, args.note)
            print(f"operação #{args.trade_id} registrada; saldo {db.account()['balance']:.2f}")
        elif args.cmd == "set-balance":
            db.set_balance(args.balance)
            print(f"saldo atualizado para {args.balance:.2f}")
        elif args.cmd == "confirm-demo":
            db.confirm_demo(True)
            print("conta DEMO confirmada pelo usuário")
        elif args.cmd == "status":
            acc = dict(db.account())
            print(json.dumps({"account": acc, "open_trades": [dict(t) for t in db.open_trades()]},
                             indent=2, ensure_ascii=False))
            return 0
        path = dashboard.write(db, cfg)
        print(f"painel: {path.relative_to(ROOT)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
