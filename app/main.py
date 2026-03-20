import os
import json
import asyncio
import httpx
import sqlite3
import traceback
import secrets
import time
from datetime import datetime, timedelta


from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.responses import Response
from authlib.integrations.starlette_client import OAuth
from fastapi import UploadFile, File
from fastapi.staticfiles import StaticFiles
from typing import Optional
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy import func
from .db import init_db, session
from .models import Zoo, Visit, Photo, UserProfile, Inquiry
from .crud import (
    list_zoos, set_visited, set_note, set_visited_at, set_visit_count,
    set_want_to_go, upsert_user_profile, create_inquiry, list_inquiries,
)
from .import_csv import import_csv
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse
from uuid import uuid4
from collections import deque




BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me")
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "").lower() in ("1", "true", "yes")
BUILD = os.getenv("BUILD", "dev")


def require_key(request: Request):
    # API以外（静的）は触らない
    if not request.url.path.startswith("/api/"):
        return

    # 認証不要API
    if request.url.path in ("/api/health", "/api/me", "/api/csrf"):
        return
    if request.url.path.startswith("/api/public/"):
        return

    # Googleログイン済みならOK
    uid = (request.session.get("user_id") or "").strip()
    if uid:
        return

    # 未ログインは401（500にしない）
    raise HTTPException(401, "Not logged in")


def get_user_id(request: Request) -> str:
    uid = (request.session.get("user_id") or "").strip()
    if not uid:
        raise HTTPException(401, "Not logged in")
    return uid

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            require_key(request)
            return await call_next(request)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        except Exception as e:
            traceback.print_exc()
            if DEBUG_ERRORS:
                return JSONResponse(
                    status_code=500,
                    content={"detail": "Internal Server Error", "type": type(e).__name__, "msg": str(e)},
                )
            return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


app = FastAPI()

# ===== Rate limit (in-memory) =====
_rl = {}  # key -> deque[timestamps]

def _hit(key: str, limit: int, window_sec: int) -> bool:
    now = time.time()
    q = _rl.get(key)
    if q is None:
        q = deque()
        _rl[key] = q
    # 古いログを捨てる
    cutoff = now - window_sec
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 対象を絞る（APIと認証のみ）
        if path.startswith("/api/") or path.startswith("/auth/"):
            ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "unknown")

            # ルール：写真アップロードは厳しめ
            if request.method == "POST" and "/photos" in path:
                ok = _hit(f"up:{ip}", limit=10, window_sec=60)  # 1分10回
            # 認証も厳しめ
            elif path.startswith("/auth/"):
                ok = _hit(f"auth:{ip}", limit=30, window_sec=60) # 1分30回
            else:
                ok = _hit(f"api:{ip}", limit=120, window_sec=60) # 1分120回

            if not ok:
                return JSONResponse({"detail": "Too Many Requests"}, status_code=429)

        return await call_next(request)

# ===== CSRF =====
CSRF_KEY = "csrf_token"

def ensure_csrf_token(request: Request) -> str:
    tok = request.session.get(CSRF_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session[CSRF_KEY] = tok
    return tok

class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # API以外・GETなどはスルー
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        path = request.url.path

        if path == "/api/csrf":
            return await call_next(request)

        # 公開APIはCSRF不要（未ログインで使う前提）
        if path.startswith("/api/public/"):
            return await call_next(request)

        # auth系（コールバック等）もCSRF不要
        if path.startswith("/auth/"):
            return await call_next(request)

        # /api/ の更新系だけCSRF要求
        if path.startswith("/api/"):
            # ログインしてないなら、そもそも更新系は拒否（保険）
            uid = request.session.get("user_id")
            if not uid:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)

            expected = ensure_csrf_token(request)
            got = request.headers.get("X-CSRF-Token") or ""
            if got != expected:
                return JSONResponse({"detail": "CSRF token invalid"}, status_code=403)

        return await call_next(request)

@app.get("/api/csrf")
def csrf_token(request: Request):
    # フロントが最初に叩いて token を受け取る
    tok = ensure_csrf_token(request)
    return {"token": tok}

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)

        # クリックジャッキング防止
        response.headers["X-Frame-Options"] = "DENY"
        # MIME sniffing防止
        response.headers["X-Content-Type-Options"] = "nosniff"
        # 参照元の漏れを減らす
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HTTPS運用なら有効（Renderは基本HTTPS）
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        # 余計な機能を制限（必要なら後で緩める）
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response

# ===== middleware order (IMPORTANT) =====
# 実行順（外→内）を  SecurityHeaders → Session → Auth → CSRF → RateLimit  にする
# add_middleware は「後から追加したものほど外側」になるので、逆順で追加する

app.add_middleware(RateLimitMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=BASE_URL.startswith("https://"),
    max_age=60 * 60 * 24 * 30,
)
app.add_middleware(SecurityHeadersMiddleware)

# ===== photo uploads =====
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# /uploads/... で画像を返せるようにする
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

BASE_DIR = Path(__file__).resolve().parent
CANDIDATES = [
    BASE_DIR / "web",        # main.py と同じ階層に web がある場合
    BASE_DIR.parent / "web", # 1つ上（リポジトリ直下）に web がある場合
]
WEB_DIR = next((p for p in CANDIDATES if p.exists()), CANDIDATES[0])



oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
)

async def geocode(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "jsonv2", "limit": 1}
    headers = {"User-Agent": "zoo-stamp-app/1.0"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])




@app.on_event("startup")
def on_startup():
    init_db()
    # 初回だけ自動インポートしたい場合：CSV_PATH をRenderの環境変数にセットしておく
    csv_path = os.getenv("CSV_PATH", "")
    if csv_path:
        with session() as db:
            count = db.exec(select(Zoo)).first()
        if not count:
            try:
                import_csv(csv_path)
            except Exception:
                # 失敗してもサーバは起動させる（ログはRender側で見える）
                pass

@app.get("/debug/build")
def debug_build():
    return {"build": BUILD, "base_url": BASE_URL, "debug_errors": DEBUG_ERRORS}

class VisitToggleIn(BaseModel):
    visited: bool

class NoteIn(BaseModel):
    note: str

class VisitedAtIn(BaseModel):
    visited_at: Optional[str] = None  # "YYYY-MM-DD" または null

class VisitCountIn(BaseModel):
    visit_count: int

class WantToGoIn(BaseModel):
    want_to_go: bool

class VisitDatesIn(BaseModel):
    visit_dates: list[str]

@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/debug/oauth")
def debug_oauth():
    cid = os.getenv("GOOGLE_CLIENT_ID")
    csec = os.getenv("GOOGLE_CLIENT_SECRET")
    return {
        "BASE_URL": BASE_URL,
        "has_client_id": bool(cid),
        "client_id_tail": (cid[-12:] if cid else None),  # 末尾だけ（安全）
        "has_client_secret": bool(csec),
        "redirect_uri": f"{BASE_URL}/auth/callback",
    }

@app.get("/login")
async def login(request: Request):
    redirect_uri = f"{BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await oauth.google.userinfo(token=token)

    sub = userinfo["sub"]
    uid = f"google:{sub}"
    email = userinfo.get("email", "")
    name = userinfo.get("name", "")
    request.session["user_id"] = uid
    request.session["email"] = email
    request.session["name"] = name
    request.session["picture"] = userinfo.get("picture")

    # ユーザー情報をDBに保存（初回作成 or 最終ログイン時刻を更新）
    with session() as db:
        upsert_user_profile(db, uid, email, name)

    return RedirectResponse(url="/")

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/api/me")
def me(request: Request):
    uid = (request.session.get("user_id") or "").strip()
    if not uid:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "user_id": uid,
        "email": request.session.get("email"),
        "name": request.session.get("name"),
        "picture": request.session.get("picture"),
    }

@app.get("/api/stats")
def stats(request: Request):
    uid = get_user_id(request)
    with session() as db:
        total = db.exec(select(Zoo).where(Zoo.is_closed == False)).all()
        total_n = len(total)
        visited_n = db.exec(
            select(Visit)
            .join(Zoo, Visit.zoo_id == Zoo.id)
            .where(Visit.user_id == uid, Visit.visited == True, Zoo.is_closed == False)
        ).all()
        return {"total": total_n, "visited": len(visited_n)}


@app.get("/api/zoos")
def zoos(request: Request):
    uid = get_user_id(request)
    with session() as db:
        zs = list_zoos(db)
        visits = {
            v.zoo_id: v
            for v in db.exec(select(Visit).where(Visit.user_id == uid)).all()
        }
        photo_zoo_ids = set(
            p.zoo_id
            for p in db.exec(select(Photo).where(Photo.user_id == uid)).all()
        )
        out = []
        for z in zs:
            v = visits.get(z.id)
            out.append({
                "id": z.id,
                "name": z.name,
                "prefecture": z.prefecture,
                "city": z.city,
                "location_raw": z.location_raw,
                "url": z.url,
                "mola_star": z.mola_star,
                "visited": bool(v.visited) if v else False,
                "visited_at": v.visited_at.isoformat() if (v and v.visited_at) else None,
                "visit_count": v.visit_count if v else 0,
                "visit_dates": json.loads(v.visit_dates) if (v and v.visit_dates) else [],
                "want_to_go": bool(v.want_to_go) if v else False,
                "note": v.note if v else "",
                "has_photos": z.id in photo_zoo_ids,
                "updated_at": v.updated_at.isoformat() if v else None,
                "lat": z.lat,
                "lng": z.lng,
                "has_elephant": bool(z.has_elephant),
                "has_giraffe": bool(z.has_giraffe),
                "has_lion": bool(z.has_lion),
                "has_tiger": bool(z.has_tiger),
                "has_panda": bool(z.has_panda),
                "has_gorilla": bool(z.has_gorilla),
                "has_hippo": bool(z.has_hippo),
                "has_koala": bool(z.has_koala),
                "has_polar_bear": bool(z.has_polar_bear),
                "has_red_panda": bool(z.has_red_panda),
                "is_closed": bool(z.is_closed),
                "closed_at": z.closed_at or "",
            })
        return out

@app.get("/api/public/zoos")
def public_zoos():
    with session() as db:
        zs = list_zoos(db)
        return [{
            "id": z.id,
            "name": z.name,
            "prefecture": z.prefecture,
            "city": z.city,
            "location_raw": z.location_raw,
            "url": z.url,
            "mola_star": z.mola_star,
            "lat": z.lat,
            "lng": z.lng,
            "has_elephant": bool(z.has_elephant),
            "has_giraffe": bool(z.has_giraffe),
            "has_lion": bool(z.has_lion),
            "has_tiger": bool(z.has_tiger),
            "has_panda": bool(z.has_panda),
            "has_gorilla": bool(z.has_gorilla),
            "has_hippo": bool(z.has_hippo),
            "has_koala": bool(z.has_koala),
            "has_polar_bear": bool(z.has_polar_bear),
            "has_red_panda": bool(z.has_red_panda),
            "is_closed": bool(z.is_closed),
            "closed_at": z.closed_at or "",
            # 公開版は visited/note は返さない（または常にfalse/空にする）
            "visited": False,
            "visited_at": None,
            "visit_count": 0,
            "visit_dates": [],
            "want_to_go": False,
            "note": "",
            "updated_at": None,
        } for z in zs]

@app.put("/api/zoos/{zoo_id}/visited")
def toggle_visited(zoo_id: int, body: VisitToggleIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        v = set_visited(db, uid, zoo_id, body.visited)
        return {"zoo_id": zoo_id, "visited": v.visited, "visited_at": v.visited_at, "visit_count": v.visit_count}


@app.put("/api/zoos/{zoo_id}/visited_at")
def update_visited_at(zoo_id: int, body: VisitedAtIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        dt = None
        if body.visited_at:
            try:
                dt = datetime.fromisoformat(body.visited_at)
            except ValueError:
                raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
        try:
            v = set_visited_at(db, uid, zoo_id, dt)
        except ValueError:
            raise HTTPException(404, "Visit record not found")
        return {
            "zoo_id": zoo_id,
            "visited_at": v.visited_at.isoformat() if v.visited_at else None,
        }


@app.put("/api/zoos/{zoo_id}/visit_count")
def update_visit_count(zoo_id: int, body: VisitCountIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        v = set_visit_count(db, uid, zoo_id, body.visit_count)
        return {"zoo_id": zoo_id, "visit_count": v.visit_count}


@app.put("/api/zoos/{zoo_id}/visit_dates")
def update_visit_dates(zoo_id: int, body: VisitDatesIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        v = db.exec(select(Visit).where(Visit.user_id == uid, Visit.zoo_id == zoo_id)).first()
        if not v:
            raise HTTPException(404, "Visit record not found")
        dates = sorted(set(body.visit_dates))
        v.visit_dates = json.dumps(dates, ensure_ascii=False)
        db.add(v)
        db.commit()
        return {"zoo_id": zoo_id, "visit_dates": dates}


@app.put("/api/zoos/{zoo_id}/want_to_go")
def update_want_to_go(zoo_id: int, body: WantToGoIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        v = set_want_to_go(db, uid, zoo_id, body.want_to_go)
        return {"zoo_id": zoo_id, "want_to_go": v.want_to_go}


@app.put("/api/zoos/{zoo_id}/note")
def update_note(zoo_id: int, body: NoteIn, request: Request):
    uid = get_user_id(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        v = set_note(db, uid, zoo_id, body.note)
        return {"zoo_id": zoo_id, "note": v.note, "updated_at": v.updated_at}



@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_index():
    """動物園リストをLD+JSONとして埋め込んで index.html を返す（SEO用）"""
    html_path = WEB_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8")

    try:
        with session() as db:
            zoo_list = list_zoos(db)

        items = []
        for i, z in enumerate(zoo_list):
            entry = {
                "@type": "TouristAttraction",
                "name": z.name,
                "address": {
                    "@type": "PostalAddress",
                    "addressRegion": z.prefecture,
                    "addressLocality": z.city,
                    "addressCountry": "JP",
                },
            }
            if z.url:
                entry["url"] = z.url
            items.append({"@type": "ListItem", "position": i + 1, "item": entry})

        ld = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": "日本全国の動物園一覧",
            "description": "全国の動物園をスタンプラリー形式で管理できるアプリ",
            "numberOfItems": len(items),
            "itemListElement": items,
        }
        ld_tag = (
            '<script type="application/ld+json">'
            + json.dumps(ld, ensure_ascii=False, separators=(",", ":"))
            + "</script>"
        )
        html = html.replace("</head>", ld_tag + "\n</head>", 1)
    except Exception:
        pass  # DB障害時はそのまま静的HTMLを返す

    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    path = WEB_DIR / "sitemap.xml"
    return FileResponse(path, media_type="application/xml")

@app.get("/api/user/photos")
def user_all_photos(request: Request):
    """ログインユーザーが投稿した全写真（園名・訪問日付き）"""
    uid = get_user_id(request)
    with session() as db:
        rows = db.exec(
            select(Photo, Zoo, Visit)
            .join(Zoo, Photo.zoo_id == Zoo.id)
            .outerjoin(Visit, (Visit.zoo_id == Photo.zoo_id) & (Visit.user_id == uid))
            .where(Photo.user_id == uid)
            .order_by(Photo.created_at.desc())
        ).all()
        return [
            {
                "id": p.id,
                "url": "/uploads/" + p.path,
                "zoo_id": p.zoo_id,
                "zoo_name": z.name,
                "visited_at": v.visited_at.strftime("%Y-%m-%d") if v and v.visited_at else None,
                "created_at": p.created_at.isoformat(),
            }
            for p, z, v in rows
        ]

@app.get("/api/zoos/{zoo_id}/photos")
def list_photos(zoo_id: int, request: Request):
    uid = get_user_id(request)
    with session() as db:
        rows = db.exec(
            select(Photo)
            .where(Photo.user_id == uid, Photo.zoo_id == zoo_id)
            .order_by(Photo.created_at.desc())
        ).all()
        return [{"id": p.id, "url": "/uploads/" + p.path, "created_at": p.created_at.isoformat()} for p in rows]

@app.post("/api/zoos/{zoo_id}/photos")
async def upload_photo(zoo_id: int, request: Request, file: UploadFile = File(...)):
    uid = get_user_id(request)

    # 画像っぽいものだけ
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "File must be an image")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"

    # 保存先：/data/uploads/{user_id}/{zoo_id}/uuid.ext
    safe_uid = uid.replace(":", "_")
    rel_dir = os.path.join(safe_uid, str(zoo_id))
    abs_dir = os.path.join(UPLOAD_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    fname = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(abs_dir, fname)

    data = await file.read()
    # ===== 5MB 制限 =====
    MAX_BYTES = 5 * 1024 * 1024
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 5MB)")

    # ===== 画像マジックバイトチェック =====
    def looks_like_image(b: bytes) -> bool:
        # JPEG
        if b.startswith(b"\xff\xd8\xff"):
            return True
        # PNG
        if b.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        # WEBP
        if b.startswith(b"RIFF") and b[8:12] == b"WEBP":
            return True
        return False

    if not looks_like_image(data):
        raise HTTPException(400, "Invalid image file")

    with open(abs_path, "wb") as f:
        f.write(data)

    rel_path = os.path.join(rel_dir, fname).replace("\\", "/")

    with session() as db:
        p = Photo(user_id=uid, zoo_id=zoo_id, path=rel_path)
        db.add(p)
        db.commit()
        db.refresh(p)

    return {"id": p.id, "url": "/uploads/" + p.path, "created_at": p.created_at.isoformat()}

@app.delete("/api/zoos/{zoo_id}/photos/{photo_id}")
def delete_photo(zoo_id: int, photo_id: int, request: Request):
    uid = get_user_id(request)

    with session() as db:
        p = db.get(Photo, photo_id)
        if not p:
            raise HTTPException(404, "Photo not found")

        # 自分の写真 & 対象動物園の写真だけ消せる
        if p.user_id != uid or p.zoo_id != zoo_id:
            raise HTTPException(403, "Forbidden")

        # 実ファイル削除
        abs_path = os.path.join(UPLOAD_DIR, p.path)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception:
            # ファイルが消せなくてもDBは消す（MVPとして）
            pass

        db.delete(p)
        db.commit()

    return {"ok": True}


# ===== Contact (お問い合わせ) =====

class InquiryIn(BaseModel):
    name: str
    email: str
    message: str


@app.post("/api/public/contact")
def post_contact(body: InquiryIn):
    with session() as db:
        inq = create_inquiry(db, body.name, body.email, body.message)
        return {"ok": True, "id": inq.id}


def require_admin(request: Request) -> str:
    """管理者チェック。非管理者は403を返す。"""
    uid = get_user_id(request)  # 未ログインは401
    admin_uid = os.getenv("ADMIN_USER_ID", "")
    if not admin_uid or uid != admin_uid:
        raise HTTPException(403, "Forbidden")
    return uid


@app.get("/api/admin/stats")
def get_admin_stats(request: Request):
    require_admin(request)
    cutoff = datetime.utcnow() - timedelta(days=30)
    with session() as db:
        total_users  = len(db.exec(select(UserProfile)).all())
        active_users = len(db.exec(select(UserProfile).where(UserProfile.last_login_at >= cutoff)).all())
        total_zoos   = len(db.exec(select(Zoo)).all())
        total_visits = len(db.exec(select(Visit).where(Visit.visited == True)).all())
        total_want   = len(db.exec(select(Visit).where(Visit.want_to_go == True)).all())
        total_photos = len(db.exec(select(Photo)).all())
        total_inq    = len(db.exec(select(Inquiry)).all())
        unread_inq   = len(db.exec(select(Inquiry).where(Inquiry.is_read == False)).all())

        # バッジ獲得者数（ユーザーごとの訪問園数で判定）
        user_visit_counts = db.exec(
            select(Visit.user_id, func.count(Visit.zoo_id).label("cnt"))
            .where(Visit.visited == True)
            .group_by(Visit.user_id)
        ).all()
        uvc = {r[0]: r[1] for r in user_visit_counts}
        badge_v10  = sum(1 for c in uvc.values() if c >= 10)
        badge_v30  = sum(1 for c in uvc.values() if c >= 30)
        badge_v50  = sum(1 for c in uvc.values() if c >= 50)

        avg_visited  = round(total_visits  / total_users, 1) if total_users else 0
        avg_want     = round(total_want    / total_users, 1) if total_users else 0

    return {
        "total_users":        total_users,
        "active_users_30d":   active_users,
        "total_zoos":         total_zoos,
        "total_visits":       total_visits,
        "total_want_to_go":   total_want,
        "total_photos":       total_photos,
        "avg_visited_per_user":   avg_visited,
        "avg_want_per_user":      avg_want,
        "badge_v10_users":    badge_v10,
        "badge_v30_users":    badge_v30,
        "badge_v50_users":    badge_v50,
        "total_inquiries":    total_inq,
        "unread_inquiries":   unread_inq,
    }


@app.get("/api/admin/contacts")
def get_contacts(request: Request):
    require_admin(request)
    with session() as db:
        rows = list_inquiries(db)
        return [
            {
                "id": r.id,
                "name": r.name,
                "email": r.email,
                "message": r.message,
                "created_at": r.created_at.isoformat(),
                "is_read": r.is_read,
            }
            for r in rows
        ]


@app.put("/api/admin/contacts/{inquiry_id}/read")
def mark_contact_read(inquiry_id: int, request: Request):
    require_admin(request)
    with session() as db:
        inq = db.get(Inquiry, inquiry_id)
        if not inq:
            raise HTTPException(404, "Inquiry not found")
        inq.is_read = True
        db.add(inq)
        db.commit()
    return {"ok": True}


@app.get("/api/admin/users")
def get_users(request: Request):
    require_admin(request)
    with session() as db:
        users = db.exec(
            select(UserProfile).order_by(UserProfile.last_login_at.desc())
        ).all()

        # 訪問済・行きたい件数をユーザーごとに集計
        visit_rows = db.exec(
            select(Visit.user_id, func.count(Visit.zoo_id).label("cnt"))
            .where(Visit.visited == True)
            .group_by(Visit.user_id)
        ).all()
        visit_map = {r[0]: r[1] for r in visit_rows}

        want_rows = db.exec(
            select(Visit.user_id, func.count(Visit.zoo_id).label("cnt"))
            .where(Visit.want_to_go == True)
            .group_by(Visit.user_id)
        ).all()
        want_map = {r[0]: r[1] for r in want_rows}

        photo_rows = db.exec(
            select(Photo.user_id, func.count(Photo.id).label("cnt"))
            .group_by(Photo.user_id)
        ).all()
        photo_map = {r[0]: r[1] for r in photo_rows}

        return [
            {
                "user_id_masked": u.user_id[:6] + "***",
                "created_at":    u.created_at.isoformat(),
                "last_login_at": u.last_login_at.isoformat(),
                "visited_count": visit_map.get(u.user_id, 0),
                "want_count":    want_map.get(u.user_id, 0),
                "photo_count":   photo_map.get(u.user_id, 0),
            }
            for u in users
        ]


    # 静的フロント（webディレクトリを確実に参照）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
