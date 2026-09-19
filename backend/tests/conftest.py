import os
import tempfile

# 必须在导入 app 之前指向测试库（engine 在 import 时创建）
_TMPDIR = tempfile.mkdtemp(prefix="seatbond_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/seatbond_test.db"
os.environ["SEED_ON_EMPTY"] = "false"

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import Hall, Showtime  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def showtime_id(client):
    """6x10 厅，过道 4、5 列；返回一个空场次的 id。"""
    db = SessionLocal()
    try:
        hall = Hall(name="测试厅", rows=6, cols=10, aisle_cols="4,5")
        db.add(hall)
        db.flush()
        st = Showtime(
            hall_id=hall.id,
            film_title="测试片",
            start_at=datetime.utcnow() + timedelta(hours=2),
        )
        db.add(st)
        db.commit()
        return st.id
    finally:
        db.close()
