from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, DeclarativeBase, sessionmaker

from app.config import get_settings

# 刮削、扫描可能与网页确认同时写库。5 秒不够：一个源网络超时就能占满写锁。
SQLITE_BUSY_TIMEOUT_MS = 30_000


class Base(DeclarativeBase):
    pass


def _configure_sqlite(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    engine = create_engine(
        url or settings.database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
        },
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


def upgrade_schema(bind=None) -> None:
    """为已有 SQLite 库补齐新增列和表（create_all 不会改旧表结构）。"""
    from sqlalchemy import text as sql_text

    import app.models  # noqa: F401  确保表已注册到 Base.metadata

    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    additions = {
        "books": [
            "ALTER TABLE books ADD COLUMN field_sources_json TEXT",
            "ALTER TABLE books ADD COLUMN translators TEXT",
        ],
        "scrape_records": [
            "ALTER TABLE scrape_records ADD COLUMN previous_values_json TEXT",
            "ALTER TABLE scrape_records ADD COLUMN applied_mode VARCHAR(32)",
        ],
    }
    with bind.connect() as conn:
        for table, stmts in additions.items():
            existing = {
                row[1]
                for row in conn.execute(sql_text(f"PRAGMA table_info({table})")).fetchall()
            }
            for stmt in stmts:
                col = stmt.rsplit(" ", 2)[0].split()[-1] if False else stmt.split("ADD COLUMN ", 1)[1].split()[0]
                if col in existing:
                    continue
                conn.execute(sql_text(stmt))
        conn.commit()
