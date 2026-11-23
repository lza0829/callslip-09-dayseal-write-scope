from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException
from app.config import TICKET_STATUSES

# 封口事件动作（事件溯源：写闸判定只能由封口事件重放得出）
SEAL_ACTION_SEAL = "封口"
SEAL_ACTION_UNSEAL = "解封"

# 全局唯一架构：全日号牌写闸。
# 封口成功后，当日一切号牌写（发号/叫号/重叫/过号/办结/作废与号段游标推进）
# 一律拒绝；只读总览与列表仍可用。全系统只贯彻这一口径，不得分叉。
SEALED_WRITE_DETAIL = "当日已封口：全日号牌写闸关闭，禁止发号、叫号、过号、办结与号段游标推进"
GATE_MODE = "全日号牌写闸"

# 写闸覆盖的号牌写口（全部共用同一闸态，同开同关）
WRITE_GATE_PORTS = ("发号", "叫号", "重叫", "过号", "办结", "作废", "游标推进")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def business_day(conn) -> str:
    return get_setting(conn, "营业日")


def seal_events(conn, day: str) -> list:
    """当日封口事件流（按 id 升序，即重放顺序）。"""
    return conn.execute(
        "SELECT * FROM day_seal_events WHERE business_day=? ORDER BY id", (day,)
    ).fetchall()


def replay_sealed(conn, day: str) -> bool:
    """由封口事件重放闸态：逐条折叠，最后一条事件决定封口与否。"""
    sealed = False
    for e in seal_events(conn, day):
        sealed = e["action"] == SEAL_ACTION_SEAL
    return sealed


def is_sealed(conn, day: str | None = None) -> bool:
    return replay_sealed(conn, day or business_day(conn))


def append_seal_event(conn, day: str, action: str, note: str = "") -> str:
    ts = now()
    conn.execute(
        "INSERT INTO day_seal_events(business_day, action, created_at, note) VALUES (?,?,?,?)",
        (day, action, ts, note),
    )
    return ts


def write_gate(conn, day: str | None = None) -> dict:
    """全日号牌写闸的对外闸态：所有写口同开同关，由同一重放结果派生。

    发号闸、叫号闸等各口闸态不得各自存储、各自判定——只能从这里取，
    从根上保证封口事件、闸状态、各写口、总览封口态同刷一致。
    """
    state = "关" if is_sealed(conn, day) else "开"
    gate = {"口径": GATE_MODE}
    for port in WRITE_GATE_PORTS:
        gate[port] = state
    return gate


def require_writable_day(conn) -> str:
    """号牌写口唯一入口闸：发号/叫号/重叫/过号/办结/作废与游标推进共用。

    必须在写事务（db(immediate=True)）内调用，使闸判定与后续写语句
    处于同一串行事务，并发封口无法插入半截写。
    """
    day = business_day(conn)
    if is_sealed(conn, day):
        raise HTTPException(400, detail=SEALED_WRITE_DETAIL)
    return day


def ticket_row(conn, tid: int):
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        raise HTTPException(404, detail="号牌不存在")
    return row


def window_row(conn, wid: int):
    row = conn.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
    if not row:
        raise HTTPException(404, detail="窗口不存在")
    return row


def kind_row(conn, kid: int):
    row = conn.execute("SELECT * FROM business_kinds WHERE id=?", (kid,)).fetchone()
    if not row:
        raise HTTPException(404, detail="业务种类不存在")
    return row


def parse_kind_ids(raw: str) -> list[int]:
    if not raw.strip():
        return []
    return [int(x) for x in raw.split(",") if x.strip()]


def require_status(status: str) -> str:
    if status not in TICKET_STATUSES:
        raise HTTPException(400, detail="号牌状态不合法")
    return status


def ticket_dict(conn, row) -> dict:
    kind = conn.execute(
        "SELECT name, prefix FROM business_kinds WHERE id=?", (row["kind_id"],)
    ).fetchone()
    win = None
    if row["window_id"]:
        w = conn.execute("SELECT name FROM windows WHERE id=?", (row["window_id"],)).fetchone()
        win = w["name"] if w else None
    return {
        "id": row["id"],
        "号牌": row["slip_no"],
        "业务种类": kind["name"] if kind else "",
        "前缀": kind["prefix"] if kind else "",
        "营业日": row["business_day"],
        "状态": row["status"],
        "窗口": win,
        "窗口编号": row["window_id"],
        "位次": row["queue_pos"],
        "取号时刻": row["taken_at"],
        "叫号时刻": row["called_at"],
        "办结时刻": row["finished_at"],
        "备注": row["note"],
    }
