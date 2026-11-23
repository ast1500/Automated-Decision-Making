import numpy as np
import pandas as pd
import pytest

from safirion_bot import indicators as ind
from safirion_bot.analysis import StrategyConfig, analyze
from safirion_bot.backtest import run_backtest
from safirion_bot.config import BotConfig, Market
from safirion_bot.db import DB
from safirion_bot.engine import day_state, record_demo_result, run_cycle
from safirion_bot.patterns import candle_patterns, support_resistance
from safirion_bot.risk import DayState, RiskConfig, RiskManager
from safirion_bot.settlement import settle_digital, settle_forex


def make_candles(closes, start="2026-01-01", freq="5min", spread=0.5):
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + spread,
                         "low": np.minimum(opens, closes) - spread, "close": closes,
                         "volume": 1.0}, index=idx)


def random_walk(n=600, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    return 100 + np.cumsum(rng.normal(drift, 0.3, n))


# --- indicadores -----------------------------------------------------------
def test_ema_constant_series():
    s = pd.Series([5.0] * 50)
    assert ind.ema(s, 9).iloc[-1] == pytest.approx(5.0)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = up[::-1].reset_index(drop=True)
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_rsi_known_value():
    # Série alternada simétrica => RSI próximo de 50
    s = pd.Series([10, 11] * 40, dtype=float)
    assert 45 < ind.rsi(s).iloc[-1] < 55


def test_atr_constant_range():
    df = make_candles([100.0] * 60, spread=1.0)
    assert ind.atr(df).iloc[-1] == pytest.approx(2.0)


def test_adx_strong_trend():
    df = make_candles(np.linspace(100, 160, 300), spread=0.2)
    out = ind.adx(df)
    assert out["adx"].iloc[-1] > 40
    assert out["plus_di"].iloc[-1] > out["minus_di"].iloc[-1]


def test_bollinger_and_macd_shapes():
    df = make_candles(random_walk(300))
    full = ind.add_all(df)
    for col in ["ema9", "ema200", "rsi", "macd_hist", "bb_upper", "atr", "adx"]:
        assert col in full and full[col].iloc[-1] == full[col].iloc[-1]  # não NaN
    assert (full["bb_upper"].dropna() >= full["bb_lower"].dropna()).all()


# --- padrões e níveis ------------------------------------------------------
def test_bullish_engulfing():
    df = pd.DataFrame({"open": [10, 10.5, 9.8], "high": [10.6, 10.6, 11.2], "low": [9.9, 9.7, 9.7],
                       "close": [10.5, 9.9, 11.0]})
    names = [n for n, _ in candle_patterns(df)]
    assert "engolfo_alta" in names


def test_support_resistance_found():
    closes = np.r_[np.linspace(100, 110, 50), np.linspace(110, 100, 50), np.linspace(100, 110, 50),
                   np.linspace(110, 105, 20)]
    lv = support_resistance(make_candles(closes, spread=0.1))
    assert lv.nearest_resistance(105) == pytest.approx(110.1, abs=0.3)


# --- análise ---------------------------------------------------------------
def test_analysis_uptrend_prefers_buy():
    closes = np.linspace(100, 130, 400) + np.sin(np.arange(400)) * 0.2
    res = analyze(make_candles(closes, spread=0.1), "TEST", "M5")
    assert res.trend == "alta"
    assert res.score_buy > res.score_sell
    assert res.signal in ("BUY", "NONE")
    assert 0 <= res.score <= 100


def test_analysis_downtrend_prefers_sell():
    closes = np.linspace(130, 100, 400) + np.sin(np.arange(400)) * 0.2
    res = analyze(make_candles(closes, spread=0.1), "TEST", "M5")
    assert res.trend == "baixa"
    assert res.score_sell > res.score_buy


def test_analysis_requires_history():
    with pytest.raises(ValueError):
        analyze(make_candles(random_walk(100)), "TEST", "M5")


def test_forex_mode_sets_stops():
    closes = np.linspace(100, 130, 400)
    res = analyze(make_candles(closes, spread=0.1), "T", "M5", StrategyConfig(modality="forex", min_score=0, min_margin=0))
    assert res.signal == "BUY"
    assert res.stop_loss < res.price < res.take_profit


# --- risco -----------------------------------------------------------------
def test_risk_stake_is_one_percent_and_no_martingale():
    rm = RiskManager()
    st = DayState("d", 10000)
    assert rm.check(st, 10000).stake == 100
    RiskManager.register_result(st, -100)
    assert rm.check(st, 9900).stake == 99  # não aumenta após perda
    with pytest.raises(ValueError):
        RiskManager(RiskConfig(allow_martingale=True))


def test_risk_limits():
    rm = RiskManager()
    st = DayState("d", 10000)
    for _ in range(3):
        RiskManager.register_result(st, -100)
    assert not rm.check(st, 9700).allowed and "consecutivas" in rm.check(st, 9700).reason

    st = DayState("d", 10000, trades=10)
    assert "limite diário" in rm.check(st, 10000).reason

    st = DayState("d", 10000, pnl=-500)
    assert "perda diária" in rm.check(st, 9500).reason

    st = DayState("d", 10000, pnl=300)
    assert "meta" in rm.check(st, 10300).reason

    st = DayState("d", 10000, open_trades=1)
    assert not rm.check(st, 10000).allowed

    # operação que ultrapassaria o limite de perda
    st = DayState("d", 10000, pnl=-450)
    assert not rm.check(st, 9550).allowed


# --- liquidação ------------------------------------------------------------
def test_settle_digital():
    df = make_candles([100, 101, 102, 103, 104])
    s = settle_digital(df, df.index[0], "BUY", 100, 10, 3, 0.85, "M5")
    assert s.status == "WIN" and s.pnl == 8.5 and s.exit_price == 103
    s = settle_digital(df, df.index[0], "SELL", 100, 10, 3, 0.85, "M5")
    assert s.status == "LOSS" and s.pnl == -10
    assert settle_digital(df, df.index[3], "BUY", 103, 10, 3, 0.85, "M5") is None


def test_settle_forex_stop_first_when_both_hit():
    df = make_candles([100, 100, 100], spread=5)
    s = settle_forex(df, df.index[0], "BUY", 100, 10, sl=97, tp=103, max_hold=10, timeframe="M5")
    assert s.status == "LOSS" and s.pnl == -10


# --- ciclo, banco e backtest ----------------------------------------------
def test_cycle_and_demo_record(tmp_path):
    cfg = BotConfig(markets=[Market("TEST", "M5")], db_path=str(tmp_path / "t.db"),
                    strategy=StrategyConfig(min_score=0, min_margin=0))
    end = pd.Timestamp.now(tz="UTC").floor("5min") - pd.Timedelta(minutes=5)
    closes = np.linspace(100, 130, 400)
    candles = make_candles(closes, start=end - pd.Timedelta(minutes=5 * 399), spread=0.1)
    db = DB(cfg.db_file)
    report = run_cycle(db, cfg, fetcher=lambda s, tf: candles)
    assert report[0]["trade"] is not None
    tid = report[0]["trade"]
    t = db.trade(tid)
    assert t["stake"] == 100 and t["status"] == "OPEN"

    # mesmo candle não gera nova operação
    report2 = run_cycle(db, cfg, fetcher=lambda s, tf: candles)
    assert report2[0]["new_candle"] is False

    record_demo_result(db, tid, "win", 85.0, screenshot="prints/1.png")
    assert db.trade(tid)["status"] == "WIN"
    assert db.account()["balance"] == pytest.approx(10085)
    assert day_state(db, cfg).trades == 1
    assert db.heartbeat("engine") is not None
    db.close()


def test_dashboard_renders(tmp_path):
    from safirion_bot import dashboard
    cfg = BotConfig(markets=[Market("TEST", "M5")], db_path=str(tmp_path / "t.db"),
                    dashboard_path=str(tmp_path / "index.html"))
    end = pd.Timestamp.now(tz="UTC").floor("5min") - pd.Timedelta(minutes=5)
    candles = make_candles(random_walk(400), start=end - pd.Timedelta(minutes=5 * 399))
    db = DB(cfg.db_file)
    run_cycle(db, cfg, fetcher=lambda s, tf: candles)
    html = dashboard.build(db, cfg)
    assert "TEST" in html and "online" in html
    db.close()


def test_backtest_runs_and_respects_daily_limit():
    df = make_candles(random_walk(1200, seed=7), freq="5min")
    res = run_backtest(df, "TEST", "M5", StrategyConfig(min_score=40, min_margin=0))
    assert res.trades > 0
    days = pd.Series([pd.Timestamp(x["time"]).tz_convert("America/Sao_Paulo").date() for x in res.log])
    assert days.value_counts().max() <= 10
    assert res.final_balance == pytest.approx(res.initial_balance + res.pnl)
