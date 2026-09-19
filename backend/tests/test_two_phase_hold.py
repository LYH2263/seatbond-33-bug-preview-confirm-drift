"""两阶段锁座：预检发令牌不落库，确认校验令牌与占用一致性。"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models.models import HoldToken, SeatHold


def _precheck(client, sid, party=3, preferred_row=None):
    body = {"showtime_id": sid, "party_size": party}
    if preferred_row:
        body["preferred_row"] = preferred_row
    return client.post("/api/holds/precheck", json=body)


def _confirm(client, token):
    return client.post("/api/holds/confirm", json={"token": token})


def _holds(client):
    return client.get("/api/holds").json()


def _insert_hold(sid, row, start_col, end_col, party, code="SB-DISTURB"):
    db = SessionLocal()
    try:
        db.add(
            SeatHold(
                showtime_id=sid,
                order_code=code,
                row=row,
                start_col=start_col,
                end_col=end_col,
                party_size=party,
            )
        )
        db.commit()
    finally:
        db.close()


def test_precheck_writes_no_hold_and_seatmap_heat_unchanged(client, showtime_id):
    before = client.get(f"/api/seatmap/{showtime_id}").json()

    r = _precheck(client, showtime_id, 3)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"]
    assert data["showtime_id"] == showtime_id
    assert data["end_col"] - data["start_col"] + 1 == 3

    # 预检不产生任何持座记录（无幽灵单）
    assert _holds(client) == []

    # 座位图热力与占用保持不变
    after = client.get(f"/api/seatmap/{showtime_id}").json()
    assert [c["occupied"] for c in after["cells"]] == [c["occupied"] for c in before["cells"]]
    assert [c["heat"] for c in after["cells"]] == [c["heat"] for c in before["cells"]]


def test_confirm_creates_exactly_one_hold(client, showtime_id):
    pre = _precheck(client, showtime_id, 3).json()
    r = _confirm(client, pre["token"])
    assert r.status_code == 200, r.text
    hold = r.json()
    assert (hold["row"], hold["start_col"], hold["end_col"]) == (
        pre["row"],
        pre["start_col"],
        pre["end_col"],
    )
    holds = _holds(client)
    assert len(holds) == 1
    assert holds[0]["id"] == hold["id"]


def test_second_confirm_with_same_token_fails(client, showtime_id):
    pre = _precheck(client, showtime_id, 2).json()
    assert _confirm(client, pre["token"]).status_code == 200

    r2 = _confirm(client, pre["token"])
    assert r2.status_code == 409
    assert len(_holds(client)) == 1


def test_interference_between_precheck_and_confirm_fails(client, showtime_id):
    pre = _precheck(client, showtime_id, 3).json()
    # 预检后他人抢先占用同一批座位
    _insert_hold(showtime_id, pre["row"], pre["start_col"], pre["end_col"], 3)

    r = _confirm(client, pre["token"])
    assert r.status_code == 409
    assert "重新预检" in r.json()["detail"]

    # 库里只有干扰那一笔，确认未落库
    holds = _holds(client)
    assert len(holds) == 1
    assert holds[0]["order_code"] == "SB-DISTURB"

    # 冲突已记录
    conflicts = client.get("/api/conflicts").json()
    assert any("已被占用" in c["reason"] for c in conflicts)


def test_expired_token_fails_and_logs_conflict(client, showtime_id):
    pre = _precheck(client, showtime_id, 2).json()
    db = SessionLocal()
    try:
        tok = db.scalar(select(HoldToken).where(HoldToken.token == pre["token"]))
        tok.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    r = _confirm(client, pre["token"])
    assert r.status_code == 409
    assert "过期" in r.json()["detail"]
    assert _holds(client) == []
    conflicts = client.get("/api/conflicts").json()
    assert any("过期" in c["reason"] for c in conflicts)


def test_confirm_unknown_token_404(client, showtime_id):
    r = _confirm(client, "not-a-real-token")
    assert r.status_code == 404


def test_precheck_honors_preferred_row(client, showtime_id):
    r = _precheck(client, showtime_id, 2, preferred_row=3)
    assert r.status_code == 200
    assert r.json()["row"] == 3


def test_precheck_no_seat_logs_conflict(client, showtime_id):
    # 6x10 厅、过道 4,5：单侧最多 3 连座，12 人必然失败
    r = _precheck(client, showtime_id, 12)
    assert r.status_code == 409
    assert _holds(client) == []
    conflicts = client.get("/api/conflicts").json()
    assert any("无足够连续空座" in c["reason"] for c in conflicts)
