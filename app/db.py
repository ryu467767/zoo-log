import os
import sqlite3 as _sqlite3
from sqlmodel import SQLModel, create_engine, Session

def get_db_path() -> str:
    # Renderでは永続ディスクを /data にマウントする（render.yaml参照）
    base = os.getenv("DB_DIR", "/data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "app.db")

DATABASE_URL = f"sqlite:///{get_db_path()}"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

def _migrate():
    """既存テーブルに新しいカラムを追加するマイグレーション。"""
    con = _sqlite3.connect(get_db_path())

    # --- カラム追加（既存カラムは無視） ---
    schema_migrations = [
        "ALTER TABLE visits ADD COLUMN visit_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE visits ADD COLUMN want_to_go INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_elephant INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_giraffe INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_lion INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_tiger INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_panda INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_gorilla INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_hippo INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_koala INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_polar_bear INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN has_red_panda INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN is_closed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE zoos ADD COLUMN closed_at TEXT",
        "ALTER TABLE visits ADD COLUMN visit_years TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE visits ADD COLUMN visit_dates TEXT NOT NULL DEFAULT '[]'",
    ]
    for sql in schema_migrations:
        try:
            con.execute(sql)
            con.commit()
        except _sqlite3.OperationalError:
            pass  # 既にカラムが存在する場合は無視

    # --- 動物フラグ seed（データは後から追加）---
    animal_seeds = []
    for sql in animal_seeds:
        try:
            con.execute(sql)
        except _sqlite3.OperationalError:
            pass
    con.commit()
    con.close()

def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate()

def session() -> Session:
    return Session(engine)
