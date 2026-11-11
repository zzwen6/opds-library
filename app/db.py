from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _configure_sqlite(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    engine = create_engine(
        url or settings.database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_engine(url: str) -> Engine:
    """测试或运行时替换全局引擎。"""
    global engine, SessionLocal
    engine = create_db_engine(url)
    SessionLocal.configure(bind=engine)
    return engine


def ensure_data_dirs() -> None:
    settings = get_settings()
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.covers_path.mkdir(parents=True, exist_ok=True)
    settings.library_path.mkdir(parents=True, exist_ok=True)


def check_wal(session: Session) -> str:
    mode = session.execute(text("PRAGMA journal_mode")).scalar_one()
    return str(mode)
