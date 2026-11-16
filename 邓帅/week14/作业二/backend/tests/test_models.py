"""SQLite 元数据模型单测（内存库）。"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mrag.models import STATUS_DONE, STATUS_PENDING, Base, FileRow


def make_session() -> sessionmaker:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_insert_and_status_flow():
    Session = make_session()
    with Session() as s:
        row = FileRow(filename="报告.pdf", file_path="data/uploads/uuid.pdf", status=STATUS_PENDING)
        s.add(row)
        s.commit()
        s.refresh(row)
        assert row.id is not None
        assert row.status == STATUS_PENDING
        assert row.chunk_count == 0

        row.status = STATUS_DONE
        row.chunk_count = 42
        s.commit()

    with Session() as s:
        got = s.get(FileRow, row.id)
        assert got.status == STATUS_DONE
        assert got.chunk_count == 42


def test_as_dict_shape():
    Session = make_session()
    with Session() as s:
        row = FileRow(filename="a.pdf", file_path="p", status=STATUS_PENDING)
        s.add(row)
        s.commit()
        s.refresh(row)
        d = row.as_dict()
        assert set(d) == {"id", "filename", "status", "error", "chunk_count", "created_at"}
        assert d["created_at"]


def test_list_all():
    Session = make_session()
    with Session() as s:
        s.add_all(
            [
                FileRow(filename="1.pdf", file_path="p1", status=STATUS_PENDING),
                FileRow(filename="2.pdf", file_path="p2", status=STATUS_DONE),
            ]
        )
        s.commit()
        names = [r.filename for r in s.scalars(select(FileRow)).all()]
        assert names == ["1.pdf", "2.pdf"]
