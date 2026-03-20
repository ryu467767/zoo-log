from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, UniqueConstraint

class Zoo(SQLModel, table=True):
    __tablename__ = "zoos"   # ★ここが超重要（テーブル名を固定）

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    prefecture: str = ""
    city: str = ""
    location_raw: str = ""
    url: str = ""
    mola_star: int = 0

    # lat/lng も使うならここで持つ（既に列ある前提）
    lat: Optional[float] = None
    lng: Optional[float] = None

    # 動物フラグ
    has_elephant: bool = Field(default=False)   # ゾウ
    has_giraffe: bool = Field(default=False)    # キリン
    has_lion: bool = Field(default=False)       # ライオン
    has_tiger: bool = Field(default=False)      # トラ
    has_panda: bool = Field(default=False)      # パンダ
    has_gorilla: bool = Field(default=False)    # ゴリラ
    has_hippo: bool = Field(default=False)      # カバ
    has_koala: bool = Field(default=False)      # コアラ
    has_polar_bear: bool = Field(default=False) # ホッキョクグマ
    has_red_panda: bool = Field(default=False)  # レッサーパンダ

    # 閉園フラグ
    is_closed: bool = Field(default=False)
    closed_at: Optional[str] = Field(default=None)  # 例: "2024-03"

    __table_args__ = (
        UniqueConstraint("name", "location_raw", "url", name="uq_zoo_identity"),
    )

class Visit(SQLModel, table=True):
    __tablename__ = "visits"  # ★固定

    # ★追加：ユーザーごとに訪問状態を分ける
    user_id: str = Field(primary_key=True, index=True)

    # ★変更：zoo_id は複合主キーの片割れにする
    zoo_id: int = Field(primary_key=True, foreign_key="zoos.id")

    visited: bool = False
    visited_at: Optional[datetime] = None
    visit_count: int = Field(default=0)
    visit_years: str = Field(default="[]")  # 旧カラム（未使用）
    visit_dates: str = Field(default="[]")  # JSON array e.g. '["2024-03-15","2025-01-20"]'
    want_to_go: bool = Field(default=False)
    note: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    user_id: str = Field(primary_key=True)
    email: str = ""
    name: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: datetime = Field(default_factory=datetime.utcnow)


class Inquiry(SQLModel, table=True):
    __tablename__ = "inquiries"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    email: str = ""
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_read: bool = Field(default=False)


class Photo(SQLModel, table=True):
    __tablename__ = "photos"

    id: Optional[int] = Field(default=None, primary_key=True)

    # ログインユーザー単位で写真を紐付け
    user_id: str = Field(index=True)

    # zoos テーブルの id に紐付け
    zoo_id: int = Field(index=True, foreign_key="zoos.id")

    # /uploads で配信する相対パスを保存
    path: str

    created_at: datetime = Field(default_factory=datetime.utcnow)
