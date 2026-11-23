"""日切封口 × 全日号牌写闸 闭合性测试。

全局唯一架构：全日号牌写闸——封口成功后，当日一切号牌写
（发号/叫号/重叫/过号/办结/作废与号段游标推进）一律拒绝；
只读总览与列表仍可用。闸态由封口事件重放得出。
"""
import threading

from fastapi.testclient import TestClient

SEAL_MSG = "当日已封口：全日号牌写闸关闭，禁止发号、叫号、过号、办结与号段游标推进"
WRITE_PORTS = ("发号", "叫号", "重叫", "过号", "办结", "作废", "游标推进")


def seal_info(c):
    r = c.get("/api/日切")
    assert r.status_code == 200
    return r.json()


def overview(c):
    r = c.get("/api/总览")
    assert r.status_code == 200
    return r.json()


def event_log(c):
    r = c.get("/api/日切/事件")
    assert r.status_code == 200
    return r.json()


def force_seal(c):
    r = c.post("/api/日切/封口", json={"强制": True})
    assert r.status_code == 200, r.text
    return r.json()


def replay(actions):
    """测试侧独立重放：折叠事件流得出闸态。"""
    sealed = False
    for a in actions:
        sealed = a == "封口"
    return sealed


def assert_gate(gate, expect):
    assert gate["口径"] == "全日号牌写闸"
    for port in WRITE_PORTS:
        assert gate[port] == expect, f"写闸端口 {port} 应为 {expect}"


def assert_views_agree(c, sealed):
    """封口事件、发号闸、叫号闸、日切页、总览封口态必须同刷。"""
    info, ov, log = seal_info(c), overview(c), event_log(c)
    expect = "关" if sealed else "开"
    assert info["已封口"] is sealed
    assert ov["已封口"] is sealed
    assert log["已封口"] is sealed
    assert_gate(info["写闸"], expect)
    assert_gate(ov["写闸"], expect)
    assert_gate(log["写闸"], expect)
    # 写闸判定须能由封口事件重放
    assert replay([e["动作"] for e in log["事件"]]) is sealed


def test_unsealed_writes_not_blocked(client):
    """未封口不得误挡：六个写口全部可用。"""
    assert_views_agree(client, sealed=False)

    r = client.post("/api/取号", json={"种类编号": 1})
    assert r.status_code == 200 and r.json()["号牌"] == "储004"

    r = client.post("/api/叫号", json={"窗口编号": 2})
    assert r.status_code == 200

    tickets = client.get("/api/号牌").json()
    busy = next(t for t in tickets if t["状态"] == "办理中")
    r = client.post(f"/api/号牌/{busy['id']}/办结")
    assert r.status_code == 200 and r.json()["状态"] == "已办结"

    r = client.post("/api/叫号", json={"窗口编号": 1})
    called = r.json()
    assert r.status_code == 200
    r = client.post(f"/api/号牌/{called['id']}/过号")
    assert r.status_code == 200 and r.json()["状态"] == "过号"
    r = client.post(f"/api/号牌/{called['id']}/重叫", json={"窗口编号": 1})
    assert r.status_code == 200 and r.json()["状态"] == "办理中"

    waiting = next(t for t in client.get("/api/号牌").json() if t["状态"] == "等候")
    r = client.post(f"/api/号牌/{waiting['id']}/作废")
    assert r.status_code == 200 and r.json()["状态"] == "已作废"


def test_seal_requires_force_with_open_tickets(client):
    r = client.post("/api/日切/封口", json={"强制": False})
    assert r.status_code == 400 and "未结号" in r.json()["detail"]
    assert_views_agree(client, sealed=False)
    info = force_seal(client)
    assert info["已封口"] is True and info["备注"] == "强制封口"
    # 重复封口幂等报错
    r = client.post("/api/日切/封口", json={"强制": True})
    assert r.status_code == 400 and r.json()["detail"] == "当日已封口"


def test_sealed_blocks_all_write_ports_reads_stay_open(client):
    force_seal(client)
    assert_views_agree(client, sealed=True)

    tickets = client.get("/api/号牌").json()
    busy = next(t for t in tickets if t["状态"] == "办理中")
    waiting = next(t for t in tickets if t["状态"] == "等候")

    writes = [
        ("/api/取号", {"种类编号": 1}),
        ("/api/叫号", {"窗口编号": 1}),
        (f"/api/号牌/{busy['id']}/过号", {}),
        (f"/api/号牌/{busy['id']}/重叫", {"窗口编号": 1}),
        (f"/api/号牌/{busy['id']}/办结", {}),
        (f"/api/号牌/{waiting['id']}/作废", {}),
    ]
    for path, body in writes:
        r = client.post(path, json=body)
        assert r.status_code == 400, f"{path} 封口后必须拒绝"
        assert r.json()["detail"] == SEAL_MSG, f"{path} 拒绝口径不统一"

    # 只读总览与列表仍可用
    for path in ("/api/总览", "/api/号牌", "/api/等候墙", "/api/叫号流水",
                 "/api/日切", "/api/日切/事件", "/api/业务种类", "/api/窗口"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} 只读必须可用"

    # 号段游标冻结：序号与封口前一致
    kinds = {k["名称"]: k["序号"] for k in client.get("/api/业务种类").json()}
    assert kinds == {"个人储蓄": 3, "对公结算": 1, "理财咨询": 1}


def test_gate_replayable_from_events(client):
    assert replay([e["动作"] for e in event_log(client)["事件"]]) is False

    force_seal(client)
    assert_views_agree(client, sealed=True)

    r = client.post("/api/日切/解封")
    assert r.status_code == 200 and r.json()["已封口"] is False
    assert_views_agree(client, sealed=False)

    # 未封口时解封 = 误挡反向，必须报错
    r = client.post("/api/日切/解封")
    assert r.status_code == 400 and r.json()["detail"] == "当日未封口"

    force_seal(client)
    log = event_log(client)
    actions = [e["动作"] for e in log["事件"]]
    assert actions == ["封口", "解封", "封口"]
    assert replay(actions) is True and log["已封口"] is True
    for e in log["事件"]:
        assert e["时刻"] and isinstance(e["备注"], str)


def test_unseal_restores_writes(client):
    """臂内解封：同一营业日内写闸随事件重放重新打开。"""
    force_seal(client)
    r = client.post("/api/取号", json={"种类编号": 1})
    assert r.status_code == 400

    r = client.post("/api/日切/解封")
    assert r.status_code == 200
    assert_views_agree(client, sealed=False)

    r = client.post("/api/取号", json={"种类编号": 1})
    assert r.status_code == 200 and r.json()["号牌"] == "储004"


def test_concurrent_seal_and_take_no_half_write(client):
    """并发封口与发号：不得已封仍发号，不得半截号牌事件。

    闸判定与写在同一 BEGIN IMMEDIATE 事务内串行：每个取号要么
    完整落在封口前（票+游标同时成立），要么被闸完整拒绝。
    """
    from app.main import app

    n_takers = 8
    barrier = threading.Barrier(n_takers + 1)
    results = []

    def take_once():
        c = TestClient(app)
        barrier.wait()
        r = c.post("/api/取号", json={"种类编号": 1})
        results.append((r.status_code, r.json()))

    def seal_once():
        c = TestClient(app)
        barrier.wait()
        results.append((c.post("/api/日切/封口", json={"强制": True}).status_code, None))

    threads = [threading.Thread(target=take_once) for _ in range(n_takers)]
    threads.append(threading.Thread(target=seal_once))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = [code for code, _ in results]
    assert 200 in codes  # 封口本身成功
    take_results = [r for r in results if r[1] is not None]
    for code, body in take_results:
        assert code in (200, 400), f"不得出现半截错误码: {code}"
        if code == 400:
            assert body["detail"] == SEAL_MSG, "失败只能是被写闸完整拒绝"

    # 无半截写：游标 == 该种类当日票数，且号段连续无空洞
    assert_views_agree(client, sealed=True)
    kinds = {k["名称"]: k["序号"] for k in client.get("/api/业务种类").json()}
    slips = sorted(
        int(t["号牌"][1:]) for t in client.get("/api/号牌").json()
        if t["前缀"] == "储"
    )
    assert slips == list(range(1, kinds["个人储蓄"] + 1))
    assert len(slips) == 3 + sum(1 for code, _ in take_results if code == 200)

    # 封口落定后再取号必须被挡
    r = client.post("/api/取号", json={"种类编号": 1})
    assert r.status_code == 400 and r.json()["detail"] == SEAL_MSG


def test_roll_day_requires_seal_and_reopens_new_day(client):
    r = client.post("/api/日切/切日", json={"营业日": "2099-01-02"})
    assert r.status_code == 400 and "须先封口" in r.json()["detail"]

    force_seal(client)
    r = client.post("/api/日切/切日", json={"营业日": "2099-01-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["营业日"] == "2099-01-02"
    assert body["封口"]["已封口"] is False  # 新营业日写闸重放为开

    assert_views_agree(client, sealed=False)
    r = client.post("/api/取号", json={"种类编号": 1})
    assert r.status_code == 200 and r.json()["号牌"] == "储001"  # 游标已重置
