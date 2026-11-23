"""Fontes de candles.

A Safirion não oferece API pública de cotações; por isso o robô usa
fontes públicas de mercado como aproximação:

* Kraken  — ``GET https://api.kraken.com/0/public/OHLC`` (cripto e EUR/USD);
* Binance — ``GET https://data-api.binance.vision/api/v3/klines`` (cripto);
* CSV     — arquivo local ``time,open,high,low,close[,volume]``.

Os preços podem diferir dos exibidos na Safirion (spreads, feed próprio e
ativos OTC sintéticos). Sempre confira o gráfico da plataforma antes de agir.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

TIMEFRAMES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}

KRAKEN_PAIRS = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD", "EURUSD": "EURUSD", "GBPUSD": "GBPUSD",
                "SOLUSD": "SOLUSD", "XRPUSD": "XRPUSD"}
BINANCE_SYMBOLS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT", "XRPUSD": "XRPUSDT"}
BINANCE_INTERVALS = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h"}

USER_AGENT = "safirion-signal-bot/0.1"


def _get_json(url: str, timeout: int = 20, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001 — repassado depois das tentativas
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"falha ao buscar {url}: {last}")


def _frame(rows, cols) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=cols)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index()


def fetch_kraken(symbol: str, timeframe: str, since: int | None = None) -> pd.DataFrame:
    """Até 720 candles. Remove o candle ainda em formação."""
    pair = KRAKEN_PAIRS.get(symbol, symbol)
    params = {"pair": pair, "interval": TIMEFRAMES[timeframe]}
    if since:
        params["since"] = since
    data = _get_json("https://api.kraken.com/0/public/OHLC?" + urllib.parse.urlencode(params))
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = [(int(r[0]), r[1], r[2], r[3], r[4], r[6]) for r in data["result"][key]]
    df = _frame(rows, ["time", "open", "high", "low", "close", "volume"])
    return drop_open_candle(df, timeframe)


def fetch_binance(symbol: str, timeframe: str, limit: int = 1000, end_ms: int | None = None) -> pd.DataFrame:
    params = {"symbol": BINANCE_SYMBOLS.get(symbol, symbol), "interval": BINANCE_INTERVALS[timeframe],
              "limit": limit}
    if end_ms:
        params["endTime"] = end_ms
    data = _get_json("https://data-api.binance.vision/api/v3/klines?" + urllib.parse.urlencode(params))
    rows = [(int(r[0]) // 1000, r[1], r[2], r[3], r[4], r[5]) for r in data]
    df = _frame(rows, ["time", "open", "high", "low", "close", "volume"])
    return drop_open_candle(df, timeframe)


def fetch_binance_history(symbol: str, timeframe: str, candles: int) -> pd.DataFrame:
    """Histórico longo para backtest (paginando para trás)."""
    frames, end = [], None
    while sum(len(f) for f in frames) < candles:
        df = fetch_binance(symbol, timeframe, 1000, end)
        if df.empty:
            break
        frames.append(df)
        end = int(df.index[0].timestamp() * 1000) - 1
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated()]
    return out.tail(candles)


def drop_open_candle(df: pd.DataFrame, timeframe: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = now or pd.Timestamp.now(tz="UTC")
    tf = pd.Timedelta(minutes=TIMEFRAMES[timeframe])
    return df[df.index + tf <= now]


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    t = df["time"]
    df["time"] = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    if "volume" not in df:
        df["volume"] = 0.0
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def fetch(source: str, symbol: str, timeframe: str) -> pd.DataFrame:
    if source == "kraken":
        return fetch_kraken(symbol, timeframe)
    if source == "binance":
        return fetch_binance(symbol, timeframe)
    raise ValueError(f"fonte desconhecida: {source}")
