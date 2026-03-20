from datetime import datetime
from sqlmodel import select
from .models import Zoo, Visit, UserProfile, Inquiry

def upsert_user_profile(db, user_id: str, email: str, name: str):
    now = datetime.utcnow()
    p = db.get(UserProfile, user_id)
    if p is None:
        p = UserProfile(user_id=user_id, email=email, name=name, created_at=now)
    else:
        p.email = email
        p.name = name
    p.last_login_at = now
    db.add(p)
    db.commit()


def create_inquiry(db, name: str, email: str, message: str):
    inq = Inquiry(name=name, email=email, message=message)
    db.add(inq)
    db.commit()
    db.refresh(inq)
    return inq


def list_inquiries(db):
    return db.exec(select(Inquiry).order_by(Inquiry.created_at.desc())).all()


def list_zoos(db):
    return db.exec(select(Zoo).order_by(Zoo.prefecture, Zoo.name)).all()

def get_visit(db, user_id: str, zoo_id: int):
    return db.exec(
        select(Visit).where(Visit.user_id == user_id, Visit.zoo_id == zoo_id)
    ).first()


def set_visited(db, user_id: str, zoo_id: int, visited: bool):
    v = get_visit(db, user_id, zoo_id)
    now = datetime.utcnow()
    if v is None:
        v = Visit(user_id=user_id, zoo_id=zoo_id)
    v.visited = visited
    v.updated_at = now
    v.visited_at = (v.visited_at or now) if visited else None  # 既存の日付を優先
    if visited:
        v.visit_count = max(1, v.visit_count)  # 訪問済みは最低1回
    else:
        v.visit_count = 0  # 解除時はリセット
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def set_visited_at(db, user_id: str, zoo_id: int, visited_at):
    v = get_visit(db, user_id, zoo_id)
    if v is None or not v.visited:
        raise ValueError("Visit record not found or not visited")
    v.visited_at = visited_at
    v.updated_at = datetime.utcnow()
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def set_visit_count(db, user_id: str, zoo_id: int, count: int):
    v = get_visit(db, user_id, zoo_id)
    now = datetime.utcnow()
    if v is None:
        v = Visit(user_id=user_id, zoo_id=zoo_id)
    v.visit_count = max(0, count)
    v.updated_at = now
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def set_want_to_go(db, user_id: str, zoo_id: int, want_to_go: bool):
    v = get_visit(db, user_id, zoo_id)
    now = datetime.utcnow()
    if v is None:
        v = Visit(user_id=user_id, zoo_id=zoo_id)
    v.want_to_go = want_to_go
    v.updated_at = now
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def set_note(db, user_id: str, zoo_id: int, note: str):
    v = get_visit(db, user_id, zoo_id)
    now = datetime.utcnow()
    if v is None:
        v = Visit(user_id=user_id, zoo_id=zoo_id)
    v.note = note
    v.updated_at = now
    db.add(v)
    db.commit()
    db.refresh(v)
    return v
