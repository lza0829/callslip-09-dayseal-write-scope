from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS business_kinds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL UNIQUE,
  seq INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind_ids TEXT NOT NULL,
  open INTEGER NOT NULL DEFAULT 1,
  current_ticket_id INTEGER
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slip_no TEXT NOT NULL,
  kind_id INTEGER NOT NULL,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL,
  window_id INTEGER,
  queue_pos INTEGER NOT NULL,
  taken_at TEXT NOT NULL,
  called_at TEXT,
  finished_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(kind_id) REFERENCES business_kinds(id),
  FOREIGN KEY(window_id) REFERENCES windows(id)
);
-- 号牌按营业日唯一：号段游标跨日重置后，新日发同段号不被旧日误挡。
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_day_slip ON tickets(business_day, slip_no);
CREATE TABLE IF NOT EXISTS call_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  window_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(ticket_id) REFERENCES tickets(id),
  FOREIGN KEY(window_id) REFERENCES windows(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- 封口事件流（追加只增）：写闸判定的唯一事实源，闸态由事件重放得出。
CREATE TABLE IF NOT EXISTS day_seal_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_day_seal_events_day ON day_seal_events(business_day, id);
'''


def connect() -> sqlite3.Connection:
    # isolation_level=None：显式掌控事务边界，由 db() 统一 BEGIN。
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def db(immediate: bool = False):
    """事务上下文。

    写路径必须 immediate=True：BEGIN IMMEDIATE 自始持有写锁，把
    「闸判定 + 全部写语句」收进同一串行事务——封口与发号/叫号等写口
    互斥，杜绝已封仍写与半截写。只读路径用默认 BEGIN 即可。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_day_seals(conn) -> None:
    """旧库迁移：把已废弃的 day_seals 投影行回填为封口事件（幂等）。

    day_seals 曾是可变的封口标志位（解封会抹掉封口时刻），无法重放。
    迁移后闸态只由 day_seal_events 重放得出，旧表不再被读写。
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='day_seals'"
    ).fetchone()
    if not has:
        return
    rows = conn.execute("SELECT * FROM day_seals").fetchall()
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for r in rows:
        done = conn.execute(
            "SELECT 1 FROM day_seal_events WHERE business_day=?",
            (r["business_day"],),
        ).fetchone()
        if done:
            continue
        if r["sealed"]:
            conn.execute(
                "INSERT INTO day_seal_events(business_day, action, created_at, note) VALUES (?,?,?,?)",
                (r["business_day"], "封口", r["sealed_at"] or ts, r["note"] or "迁移回填"),
            )
        elif r["note"] == "已解封":
            conn.execute(
                "INSERT INTO day_seal_events(business_day, action, created_at, note) VALUES (?,?,?,?)",
                (r["business_day"], "解封", ts, "迁移回填"),
            )


def _migrate_tickets_day_slip(conn) -> None:
    """旧库 tickets.slip_no 全局唯一 → 按营业日唯一（重建表，幂等）。

    全局唯一会让号段游标跨日重置后，新营业日发同段号被旧日记录误挡
    （未封口却写不进），与全日号牌写闸口径矛盾。
    """
    indexes = conn.execute("PRAGMA index_list(tickets)").fetchall()
    global_unique = any(
        i["name"].startswith("sqlite_autoindex_tickets") for i in indexes
    )
    if not global_unique:
        return
    conn.execute(
        """CREATE TABLE tickets_day_slip (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slip_no TEXT NOT NULL,
          kind_id INTEGER NOT NULL,
          business_day TEXT NOT NULL,
          status TEXT NOT NULL,
          window_id INTEGER,
          queue_pos INTEGER NOT NULL,
          taken_at TEXT NOT NULL,
          called_at TEXT,
          finished_at TEXT,
          note TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(kind_id) REFERENCES business_kinds(id),
          FOREIGN KEY(window_id) REFERENCES windows(id)
        )"""
    )
    conn.execute("INSERT INTO tickets_day_slip SELECT * FROM tickets")
    conn.execute("DROP TABLE tickets")
    conn.execute("ALTER TABLE tickets_day_slip RENAME TO tickets")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_day_slip ON tickets(business_day, slip_no)"
    )


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
    # 迁移单独持连接：重建表期间须关外键（PRAGMA 在事务外才生效）。
    conn = connect()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        _migrate_day_seals(conn)
        _migrate_tickets_day_slip(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
