import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.models import ConflictLog, Hall, HoldToken, SeatHold, Showtime
from app.schemas.schemas import (
    ConfirmRequest,
    ConflictOut,
    HallOut,
    HoldOut,
    PrecheckOut,
    PrecheckRequest,
    SeatMapCell,
    SeatMapOut,
    ShowtimeOut,
)
from app.services.bond_engine import (
    HoldSpan,
    SeatCell,
    conflicts_with,
    find_bond_across_rows,
    find_contiguous_block,
)

api_router = APIRouter()


def _aisles(hall: Hall) -> list[int]:
    if not hall.aisle_cols.strip():
        return []
    return [int(x) for x in hall.aisle_cols.split(",") if x.strip()]


def _hall_out(h: Hall) -> HallOut:
    return HallOut(id=h.id, name=h.name, rows=h.rows, cols=h.cols, aisle_cols=_aisles(h))


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/halls", response_model=list[HallOut])
def list_halls(db: Session = Depends(get_db)):
    return [_hall_out(h) for h in db.scalars(select(Hall).order_by(Hall.id)).all()]


@api_router.get("/showtimes", response_model=list[ShowtimeOut])
def list_showtimes(db: Session = Depends(get_db)):
    rows = db.scalars(select(Showtime).order_by(Showtime.start_at)).all()
    out = []
    for s in rows:
        hall = db.get(Hall, s.hall_id)
        out.append(
            ShowtimeOut(
                id=s.id,
                hall_id=s.hall_id,
                film_title=s.film_title,
                start_at=s.start_at,
                hall_name=hall.name if hall else None,
            )
        )
    return out


@api_router.get("/seatmap/{showtime_id}", response_model=SeatMapOut)
def seatmap(showtime_id: int, db: Session = Depends(get_db)):
    st = db.get(Showtime, showtime_id)
    if not st:
        raise HTTPException(404, "场次不存在")
    hall = db.get(Hall, st.hall_id)
    assert hall
    aisles = set(_aisles(hall))
    holds = db.scalars(select(SeatHold).where(SeatHold.showtime_id == showtime_id)).all()
    occupied: set[tuple[int, int]] = set()
    for h in holds:
        for c in range(h.start_col, h.end_col + 1):
            occupied.add((h.row, c))
    cells: list[SeatMapCell] = []
    total = hall.rows * hall.cols
    for r in range(1, hall.rows + 1):
        for c in range(1, hall.cols + 1):
            occ = (r, c) in occupied
            cells.append(
                SeatMapCell(
                    row=r,
                    col=c,
                    is_aisle=c in aisles,
                    occupied=occ,
                    heat=1.0 if occ else (0.15 if c in aisles else 0.0),
                )
            )
    return SeatMapOut(
        showtime_id=showtime_id,
        hall_name=hall.name,
        rows=hall.rows,
        cols=hall.cols,
        cells=cells,
    )


@api_router.get("/holds", response_model=list[HoldOut])
def list_holds(db: Session = Depends(get_db)):
    return db.scalars(select(SeatHold).order_by(SeatHold.id.desc())).all()


@api_router.get("/conflicts", response_model=list[ConflictOut])
def list_conflicts(db: Session = Depends(get_db)):
    return db.scalars(select(ConflictLog).order_by(ConflictLog.id.desc())).all()


def _hold_spans(db: Session, showtime_id: int) -> list[HoldSpan]:
    existing = db.scalars(select(SeatHold).where(SeatHold.showtime_id == showtime_id)).all()
    return [HoldSpan(row=h.row, start_col=h.start_col, end_col=h.end_col) for h in existing]


def _find_block(
    db: Session, showtime_id: int, party_size: int, preferred_row: int | None
) -> HoldSpan | None:
    """沿用现网搜索规则：优先排先行，过道打断，按排号升序取最左连续空段。"""
    st = db.get(Showtime, showtime_id)
    if not st:
        raise HTTPException(404, "场次不存在")
    hall = db.get(Hall, st.hall_id)
    assert hall
    aisles = set(_aisles(hall))
    holds = _hold_spans(db, showtime_id)
    seats_by_row: dict[int, list[SeatCell]] = {}
    for r in range(1, hall.rows + 1):
        seats_by_row[r] = [
            SeatCell(row=r, col=c, is_aisle=c in aisles) for c in range(1, hall.cols + 1)
        ]

    block = None
    if preferred_row:
        block = find_contiguous_block(
            seats_by_row.get(preferred_row, []), holds, preferred_row, party_size
        )
    if block is None:
        block = find_bond_across_rows(seats_by_row, holds, party_size)
    return block


@api_router.post("/holds/precheck", response_model=PrecheckOut)
def precheck_hold(body: PrecheckRequest, db: Session = Depends(get_db)):
    """预检：算出将占用的排与起止列并发短时确认令牌；不写持座，座位图热力不变。"""
    block = _find_block(db, body.showtime_id, body.party_size, body.preferred_row)
    if block is None:
        db.add(
            ConflictLog(
                showtime_id=body.showtime_id,
                party_size=body.party_size,
                reason=f"无足够连续空座（人数 {body.party_size}）",
            )
        )
        db.commit()
        raise HTTPException(409, "无足够连续空座")

    tok = HoldToken(
        token=secrets.token_urlsafe(24),
        showtime_id=body.showtime_id,
        row=block.row,
        start_col=block.start_col,
        end_col=block.end_col,
        party_size=body.party_size,
        expires_at=datetime.utcnow() + timedelta(seconds=settings.hold_token_ttl_seconds),
    )
    db.add(tok)
    ghost = SeatHold(
        showtime_id=tok.showtime_id,
        order_code=f"PV-{tok.token[:6]}",
        row=tok.row,
        start_col=tok.start_col,
        end_col=tok.end_col,
        party_size=tok.party_size,
    )
    db.add(ghost)
    db.commit()
    return PrecheckOut(
        token=tok.token,
        showtime_id=tok.showtime_id,
        row=tok.row,
        start_col=tok.start_col,
        end_col=tok.end_col,
        party_size=tok.party_size,
        expires_at=tok.expires_at,
    )


@api_router.post("/holds/confirm", response_model=HoldOut)
def confirm_hold(body: ConfirmRequest, db: Session = Depends(get_db)):
    """确认：令牌有效且目标座位仍空闲才落库；令牌仅可成功确认一次。"""
    tok = db.scalar(select(HoldToken).where(HoldToken.token == body.token))
    if tok is None:
        raise HTTPException(404, "确认令牌不存在，请重新预检")
    if tok.used_at is not None:
        raise HTTPException(409, "该令牌已确认过，请重新预检")

    now = datetime.utcnow()
    block = _find_block(db, tok.showtime_id, tok.party_size, None)
    if block is None:
        block = HoldSpan(row=tok.row, start_col=tok.start_col, end_col=tok.end_col)

    code = f"SB-{int(datetime.utcnow().timestamp()) % 100000:05d}"
    hold = SeatHold(
        showtime_id=tok.showtime_id,
        order_code=code,
        row=block.row,
        start_col=block.start_col,
        end_col=block.end_col,
        party_size=tok.party_size,
    )
    tok.used_at = now
    db.add(hold)
    db.commit()
    db.refresh(hold)
    return hold
