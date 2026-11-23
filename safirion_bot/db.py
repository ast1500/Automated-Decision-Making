"""Persistência em SQLite (arquivo versionado no git para sobreviver a reinícios)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL,
    initial_balance REAL NOT NULL,
    demo_confirmed INTEGER NOT NULL DEFAULT 0,
    demo_confirmed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    price REAL,
    signal TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_buy INTEGER,
    score_sell INTEGER,
    setup TEXT,
    trend TEXT,
    regime TEXT,
    payload TEXT NOT NULL,
    UNIQUE (symbol, timeframe, candle_time)
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER REFERENCES analyses(id),
    opened_at TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    modality TEXT NOT NULL,
    stake REAL NOT NULL,
    balance_before REAL NOT NULL,
    entry_price REAL NOT NULL,
    expiry_candles INTEGER,
    stop_loss REAL,
    take_profit REAL,
    score INTEGER,
    status TEXT NOT NULL DEFAULT 'OPEN',      -- OPEN | WIN | LOSS | DRAW
    exit_price REAL,
    closed_at TEXT,
    sim_pnl REAL,                             -- resultado simulado com dados públicos
    demo_result TEXT,                         -- resultado real informado da conta DEMO
    demo_pnl REAL,
    pnl REAL,                                 -- resultado efetivo (DEMO se informado, senão simulado)
    screenshot TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS heartbeat (
    component TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL,
    info TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    # --- conta -----------------------------------------------------------
    def ensure_account(self, initial_balance: float) -> sqlite3.Row:
        row = self.account()
        if row is None:
            with self.tx() as c:
                c.execute("INSERT INTO account (id, balance, initial_balance, updated_at) VALUES (1, ?, ?, ?)",
                          (initial_balance, initial_balance, now_iso()))
            row = self.account()
        return row

    def account(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM account WHERE id = 1").fetchone()

    def set_balance(self, balance: float):
        with self.tx() as c:
            c.execute("UPDATE account SET balance = ?, updated_at = ? WHERE id = 1", (round(balance, 2), now_iso()))

    def add_to_balance(self, delta: float):
        with self.tx() as c:
            c.execute("UPDATE account SET balance = ROUND(balance + ?, 2), updated_at = ? WHERE id = 1",
                      (delta, now_iso()))

    def confirm_demo(self, confirmed: bool = True):
        with self.tx() as c:
            c.execute("UPDATE account SET demo_confirmed = ?, demo_confirmed_at = ? WHERE id = 1",
                      (int(confirmed), now_iso() if confirmed else None))

    # --- análises --------------------------------------------------------
    def save_analysis(self, res) -> int:
        d = res.to_dict()
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO analyses (created_at, symbol, timeframe, candle_time, price, signal, score,
                   score_buy, score_sell, setup, trend, regime, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (symbol, timeframe, candle_time) DO NOTHING""",
                (now_iso(), res.symbol, res.timeframe, res.time, res.price, res.signal, res.score,
                 res.score_buy, res.score_sell, res.setup, res.trend, res.regime, json.dumps(d)))
            if cur.rowcount == 0:
                return -1  # candle já analisado
            return cur.lastrowid

    def latest_analyses(self, limit: int = 50):
        return self.conn.execute("SELECT * FROM analyses ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def latest_per_market(self):
        return self.conn.execute(
            """SELECT a.* FROM analyses a JOIN (SELECT symbol, timeframe, MAX(id) mid FROM analyses
               GROUP BY symbol, timeframe) m ON a.id = m.mid ORDER BY a.symbol, a.timeframe""").fetchall()

    # --- operações -------------------------------------------------------
    def open_trade(self, **kw) -> int:
        cols = ", ".join(kw)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO trades ({cols}) VALUES ({', '.join('?' * len(kw))})", tuple(kw.values()))
            return cur.lastrowid

    def update_trade(self, trade_id: int, **kw):
        sets = ", ".join(f"{k} = ?" for k in kw)
        with self.tx() as c:
            c.execute(f"UPDATE trades SET {sets} WHERE id = ?", (*kw.values(), trade_id))

    def trade(self, trade_id: int):
        return self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()

    def open_trades(self, symbol: str | None = None, timeframe: str | None = None):
        q, args = "SELECT * FROM trades WHERE status = 'OPEN'", []
        if symbol:
            q += " AND symbol = ?"; args.append(symbol)
        if timeframe:
            q += " AND timeframe = ?"; args.append(timeframe)
        return self.conn.execute(q, args).fetchall()

    def trades(self, limit: int = 200):
        return self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def trades_since(self, since_iso: str):
        return self.conn.execute("SELECT * FROM trades WHERE opened_at >= ? ORDER BY id", (since_iso,)).fetchall()

    # --- heartbeat -------------------------------------------------------
    def beat(self, component: str, info: dict | None = None):
        with self.tx() as c:
            c.execute("""INSERT INTO heartbeat (component, last_seen, info) VALUES (?,?,?)
                         ON CONFLICT (component) DO UPDATE SET last_seen = excluded.last_seen, info = excluded.info""",
                      (component, now_iso(), json.dumps(info or {})))

    def heartbeat(self, component: str):
        return self.conn.execute("SELECT * FROM heartbeat WHERE component = ?", (component,)).fetchone()
