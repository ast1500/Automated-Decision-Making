"""Gera o painel estático (``docs/index.html``) a partir do SQLite."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, BotConfig
from .db import DB
from .engine import day_state

ONLINE_MINUTES = 30  # heartbeat mais antigo que isso => offline


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _money(x) -> str:
    return "—" if x is None else f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _equity_svg(points: list[float]) -> str:
    if len(points) < 2:
        return '<p class="muted">Sem operações liquidadas ainda.</p>'
    w, h, pad = 640, 160, 8
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    xs = [pad + i * (w - 2 * pad) / (len(points) - 1) for i in range(len(points))]
    ys = [h - pad - (p - lo) * (h - 2 * pad) / span for p in points]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Evolução do saldo">'
            f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"/></svg>'
            f'<p class="muted small">mín {_money(lo)} · máx {_money(hi)}</p>')


def _score_bar(label: str, v: int, cls: str) -> str:
    return (f'<div class="bar"><span>{label}</span><div class="track"><div class="fill {cls}" '
            f'style="width:{max(0, min(100, v))}%"></div></div><b>{v}</b></div>')


def build(db: DB, cfg: BotConfig) -> str:
    acc = db.account()
    hb = db.heartbeat("engine")
    now = datetime.now(timezone.utc)
    online, last_seen, hb_info = False, "nunca", {}
    if hb:
        seen = datetime.fromisoformat(hb["last_seen"])
        online = (now - seen).total_seconds() < ONLINE_MINUTES * 60
        last_seen = seen.strftime("%d/%m %H:%M UTC")
        hb_info = json.loads(hb["info"] or "{}")
    st = day_state(db, cfg) if acc else None
    trades = db.trades(200)
    closed = [t for t in reversed(trades) if t["status"] != "OPEN"]
    equity = ([acc["initial_balance"]] + [t["balance_before"] + (t["pnl"] or 0) for t in closed]) if acc else []
    wins = sum(t["status"] == "WIN" for t in closed)
    losses = sum(t["status"] == "LOSS" for t in closed)

    cards = []
    for a in db.latest_per_market():
        p = json.loads(a["payload"])
        ind = p["indicators"]
        sig_cls = {"BUY": "buy", "SELL": "sell"}.get(p["signal"], "none")
        sig_txt = {"BUY": "COMPRA ▲", "SELL": "VENDA ▼"}.get(p["signal"], "SEM OPERAÇÃO")
        extra = (f'expiração {p["expiry_candles"]} candles' if p["modality"] == "digital"
                 else f'SL {p["stop_loss"]} · TP {p["take_profit"]}') if p["signal"] != "NONE" else ""
        rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in [
            ("EMA 9 / 21", f'{ind["ema9"]} / {ind["ema21"]}'), ("EMA 50 / 200", f'{ind["ema50"]} / {ind["ema200"]}'),
            ("RSI 14", ind["rsi"] and round(ind["rsi"], 1)),
            ("MACD / sinal / hist", f'{ind["macd"]:.5g} / {ind["macd_signal"]:.5g} / {ind["macd_hist"]:.5g}'),
            ("Bollinger sup / méd / inf", f'{ind["bb_upper"]:.6g} / {ind["bb_mid"]:.6g} / {ind["bb_lower"]:.6g}'),
            ("ATR 14", f'{ind["atr"]:.5g}'),
            ("ADX / +DI / −DI", f'{ind["adx"]:.1f} / {ind["plus_di"]:.1f} / {ind["minus_di"]:.1f}'),
            ("Suporte / Resistência", f'{p["support"]} / {p["resistance"]}'),
            ("Padrões", ", ".join(p["patterns"]) or "—")])
        cards.append(f"""
<article class="card">
  <header><h3>{_e(p["symbol"])} <small>{_e(p["timeframe"])}</small></h3>
  <span class="pill {sig_cls}">{sig_txt}</span></header>
  <p class="muted small">candle {_e(p["time"])} · preço {_e(p["price"])}</p>
  <p>Tendência <b>{_e(p["trend"])}</b> · regime <b>{_e(p["regime"])}</b> · setup <b>{_e(p["setup"])}</b>
  {f'<br><span class="small">{_e(extra)}</span>' if extra else ''}</p>
  {_score_bar("Compra", p["score_buy"], "buy")}{_score_bar("Venda", p["score_sell"], "sell")}
  <ul class="reasons">{"".join(f"<li>{_e(r)}</li>" for r in p["reasons"]) or "<li>—</li>"}</ul>
  <details><summary>Indicadores</summary><table>{rows}</table></details>
</article>""")

    trade_rows = "".join(f"""<tr>
<td>#{t["id"]}</td><td>{_e(t["opened_at"][:16].replace("T", " "))}</td><td>{_e(t["symbol"])} {_e(t["timeframe"])}</td>
<td class="{'buy' if t['direction'] == 'BUY' else 'sell'}">{'COMPRA' if t['direction'] == 'BUY' else 'VENDA'}</td>
<td>{t["score"]}</td><td>{_money(t["stake"])}</td><td>{_e(t["entry_price"])}</td><td>{_e(t["exit_price"])}</td>
<td><span class="pill {(t['status'] or '').lower()}">{_e(t["status"])}</span></td>
<td>{_money(t["sim_pnl"])}</td><td>{_e(t["demo_result"] or "—")} {_money(t["demo_pnl"]) if t["demo_pnl"] is not None else ""}</td>
<td>{f'<a href="{_e(t["screenshot"])}">ver</a>' if t["screenshot"] else "—"}</td></tr>""" for t in trades)

    hist_rows = "".join(f"""<tr><td>{_e(a["candle_time"][:16].replace("T", " "))}</td><td>{_e(a["symbol"])} {_e(a["timeframe"])}</td>
<td>{_e(a["signal"])}</td><td>{a["score"]}</td><td>{a["score_buy"]}/{a["score_sell"]}</td><td>{_e(a["trend"])}</td>
<td>{_e(a["regime"])}</td></tr>""" for a in db.latest_analyses(60))

    bt_blocks = []
    for f in sorted((ROOT / "data").glob("backtest_*.json")):
        b = json.loads(f.read_text())
        bt_blocks.append(f"""<tr><td>{_e(b["symbol"])} {_e(b["timeframe"])}</td><td class="small">{_e(b["period"])}</td>
<td>{b["trades"]}</td><td>{b["win_rate"] * 100:.1f}%</td>
<td>{"—" if b["breakeven_win_rate"] is None else f'{b["breakeven_win_rate"] * 100:.1f}%'}</td>
<td>{b["return_pct"]:+.2f}%</td><td>{b["max_drawdown_pct"]:.2f}%</td><td>{_e(b["profit_factor"])}</td></tr>""")

    risk = cfg.risk
    if st:
        loss_used = max(0.0, -st.pnl) / (risk.daily_loss_limit_pct * st.start_balance) * 100
        gain_used = max(0.0, st.pnl) / (risk.daily_target_pct * st.start_balance) * 100
        risk_html = f"""
<div class="kv"><span>Operações hoje</span><b>{st.trades} / {risk.max_trades_per_day}</b></div>
<div class="kv"><span>Perdas consecutivas</span><b>{st.consecutive_losses} / {risk.max_consecutive_losses}</b></div>
<div class="kv"><span>Resultado do dia</span><b>{_money(st.pnl)}</b></div>
<div class="kv"><span>Limite de perda (5%) usado</span><b>{loss_used:.0f}%</b></div>
<div class="kv"><span>Meta (3%) atingida</span><b>{gain_used:.0f}%</b></div>
<div class="kv"><span>Valor por operação</span><b>{_money(round(acc["balance"] * risk.stake_pct, 2))} ({risk.stake_pct:.0%})</b></div>
<div class="kv"><span>Martingale / Gale</span><b>desativado</b></div>"""
    else:
        risk_html = '<p class="muted">Sem dados.</p>'

    errors = hb_info.get("errors") or []
    generated = now.strftime("%d/%m/%Y %H:%M UTC")
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Safirion Bot — Painel</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--muted:#5f6b7a;--line:#e3e6ea;--accent:#2563eb;--buy:#15803d;--sell:#b91c1c;--warn:#b45309}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1216;--card:#171b21;--fg:#e7eaee;--muted:#98a2b3;--line:#2a3039;--accent:#60a5fa;--buy:#4ade80;--sell:#f87171;--warn:#fbbf24}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:0}}h2{{font-size:16px;margin:24px 0 8px}}h3{{margin:0;font-size:16px}}
.top{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}}
.grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
.stats{{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card header{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.stat b{{display:block;font-size:20px}}.muted{{color:var(--muted)}}.small{{font-size:12px}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid currentColor}}
.buy,.win{{color:var(--buy)}}.sell,.loss{{color:var(--sell)}}.none,.draw{{color:var(--muted)}}.open{{color:var(--accent)}}
.on{{color:var(--buy)}}.off{{color:var(--sell)}}.warn{{color:var(--warn)}}
.bar{{display:grid;grid-template-columns:60px 1fr 32px;gap:8px;align-items:center;margin:4px 0}}
.track{{height:8px;background:var(--line);border-radius:4px;overflow:hidden}}.fill{{height:100%}}.fill.buy{{background:var(--buy)}}.fill.sell{{background:var(--sell)}}
.reasons{{margin:8px 0;padding-left:18px;font-size:13px}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-weight:600}}
.kv{{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px dashed var(--line)}}
.chart{{width:100%;height:160px}}.notice{{border-left:3px solid var(--warn)}}
</style></head><body><main>
<div class="top"><h1>Safirion Bot · modo sinais</h1><span class="muted small">gerado {generated} · atualiza a cada 60 s</span></div>

<div class="card notice" style="margin-top:12px"><b>Execução manual.</b> A Safirion não oferece integração autorizada;
o robô não envia ordens. Os sinais devem ser executados por você na conta DEMO, e o resultado registrado com
<code>python -m safirion_bot record</code>. Resultados “simulados” usam cotações públicas ({_e(cfg.source)}), que podem diferir da Safirion.</div>

<h2>Status</h2>
<div class="stats">
<div class="card stat"><span class="muted">Robô</span><b class="{'on' if online else 'off'}">{'● online' if online else '● offline'}</b><span class="small muted">último ciclo {last_seen}</span></div>
<div class="card stat"><span class="muted">Conta DEMO</span><b class="{'on' if acc and acc['demo_confirmed'] else 'warn'}">{'confirmada' if acc and acc['demo_confirmed'] else 'não confirmada'}</b><span class="small muted">confirmação manual do usuário</span></div>
<div class="card stat"><span class="muted">Saldo DEMO</span><b>{_money(acc["balance"]) if acc else "—"}</b><span class="small muted">inicial {_money(acc["initial_balance"]) if acc else "—"}</span></div>
<div class="card stat"><span class="muted">Operações</span><b>{len(closed)}</b><span class="small muted">{wins} ganhos · {losses} perdas</span></div>
<div class="card stat"><span class="muted">Taxa de acerto</span><b>{(wins / (wins + losses) * 100 if wins + losses else 0):.1f}%</b><span class="small muted">payout configurado {cfg.payout:.0%}</span></div>
</div>
{f'<p class="warn small">Erros no último ciclo: {_e("; ".join(errors))}</p>' if errors else ''}

<h2>Mercados analisados</h2>
<div class="grid">{"".join(cards) or '<p class="muted">Nenhuma análise ainda.</p>'}</div>

<h2>Risco do dia</h2>
<div class="grid"><div class="card">{risk_html}</div><div class="card"><b>Evolução do saldo</b>{_equity_svg(equity)}</div></div>

<h2>Operações</h2>
<div class="card scroll"><table><thead><tr><th>#</th><th>Abertura (UTC)</th><th>Ativo</th><th>Direção</th><th>Score</th><th>Valor</th>
<th>Entrada</th><th>Saída</th><th>Status</th><th>Simulado</th><th>DEMO real</th><th>Print</th></tr></thead>
<tbody>{trade_rows or '<tr><td colspan="12" class="muted">Nenhuma operação.</td></tr>'}</tbody></table></div>

<h2>Backtests</h2>
<div class="card scroll"><table><thead><tr><th>Mercado</th><th>Período</th><th>Operações</th><th>Acerto</th><th>Equilíbrio</th>
<th>Retorno</th><th>Drawdown máx.</th><th>Profit factor</th></tr></thead>
<tbody>{"".join(bt_blocks) or '<tr><td colspan="8" class="muted">Nenhum backtest salvo.</td></tr>'}</tbody></table></div>

<h2>Histórico de análises</h2>
<div class="card scroll"><table><thead><tr><th>Candle (UTC)</th><th>Ativo</th><th>Sinal</th><th>Score</th><th>Compra/Venda</th>
<th>Tendência</th><th>Regime</th></tr></thead><tbody>{hist_rows or '<tr><td colspan="7" class="muted">—</td></tr>'}</tbody></table></div>
</main></body></html>"""


def write(db: DB, cfg: BotConfig) -> Path:
    out = cfg.dashboard_file
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(db, cfg), encoding="utf-8")
    return out
