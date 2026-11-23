from __future__ import annotations
from datetime import date, timedelta
from fastapi import HTTPException
from app.services.core import (
    SEAL_ACTION_SEAL,
    SEAL_ACTION_UNSEAL,
    append_seal_event,
    business_day,
    is_sealed,
    seal_events,
    write_gate,
)

OPEN_STATUSES = ("等候", "叫号中", "办理中", "过号")


def _open_count(conn, day: str) -> int:
    return conn.execute(
        f"""SELECT COUNT(*) AS c FROM tickets
            WHERE business_day=? AND status IN ({','.join('?' * len(OPEN_STATUSES))})""",
        (day, *OPEN_STATUSES),
    ).fetchone()["c"]


def get_seal(conn) -> dict:
    """日切页数据：封口态与写闸同源于封口事件重放。"""
    day = business_day(conn)
    events = seal_events(conn, day)
    sealed = bool(events) and events[-1]["action"] == SEAL_ACTION_SEAL
    last_seal = next(
        (e for e in reversed(events) if e["action"] == SEAL_ACTION_SEAL), None
    )
    return {
        "营业日": day,
        "已封口": sealed,
        "封口时刻": last_seal["created_at"] if sealed and last_seal else None,
        "备注": events[-1]["note"] if events else "当日未封口",
        "未结号数": _open_count(conn, day),
        "写闸": write_gate(conn, day),
    }


def seal_event_log(conn) -> dict:
    """封口事件流水：写闸判定可由此外部重放验证。"""
    day = business_day(conn)
    return {
        "营业日": day,
        "已封口": is_sealed(conn, day),
        "写闸": write_gate(conn, day),
        "事件": [
            {"id": e["id"], "动作": e["action"], "时刻": e["created_at"], "备注": e["note"]}
            for e in seal_events(conn, day)
        ],
    }


def seal_day(conn, force: bool = False) -> dict:
    day = business_day(conn)
    if is_sealed(conn, day):
        raise HTTPException(400, detail="当日已封口")
    open_count = _open_count(conn, day)
    if open_count and not force:
        raise HTTPException(400, detail=f"仍有{open_count}个未结号，须强制封口或先清队")
    note = "强制封口" if force and open_count else "正常封口"
    append_seal_event(conn, day, SEAL_ACTION_SEAL, note)
    return get_seal(conn)


def unseal_day(conn) -> dict:
    """臂内解封：同一营业日内追加解封事件，写闸随重放结果同开。"""
    day = business_day(conn)
    if not is_sealed(conn, day):
        raise HTTPException(400, detail="当日未封口")
    append_seal_event(conn, day, SEAL_ACTION_UNSEAL, "当日解封")
    return get_seal(conn)


def roll_day(conn, target: str | None = None) -> dict:
    cur = business_day(conn)
    if not is_sealed(conn, cur):
        raise HTTPException(400, detail="须先封口当日队列再切日")
    nxt = target or (date.fromisoformat(cur) + timedelta(days=1)).isoformat()
    if nxt <= cur:
        raise HTTPException(400, detail="新营业日须晚于当前营业日")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES ('营业日', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (nxt,),
    )
    # 清空窗口当前号引用（跨日）
    conn.execute("UPDATE windows SET current_ticket_id=NULL")
    # 重置各类序号
    conn.execute("UPDATE business_kinds SET seq=0")
    return {
        "原营业日": cur,
        "营业日": nxt,
        "封口": get_seal(conn),
    }


def overview(conn) -> dict:
    day = business_day(conn)
    def cnt(status: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE business_day=? AND status=?",
            (day, status),
        ).fetchone()["c"]

    wins = conn.execute(
        "SELECT id, name, open, current_ticket_id FROM windows ORDER BY id"
    ).fetchall()
    return {
        "营业日": day,
        "已封口": is_sealed(conn, day),
        "写闸": write_gate(conn, day),
        "等候": cnt("等候"),
        "叫号中": cnt("叫号中"),
        "办理中": cnt("办理中"),
        "过号": cnt("过号"),
        "已办结": cnt("已办结"),
        "已作废": cnt("已作废"),
        "开放窗口": sum(1 for w in wins if w["open"]),
        "窗口": [
            {
                "id": w["id"],
                "名称": w["name"],
                "开放": bool(w["open"]),
                "有在办": bool(w["current_ticket_id"]),
            }
            for w in wins
        ],
    }
