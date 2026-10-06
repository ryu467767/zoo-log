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
from typing import Optional, List
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy import func
from .db import init_db, session
from .models import Zoo, Visit, Photo, UserProfile, Inquiry, ShareCard
from .crud import (
    list_zoos, set_visited, set_note, set_visited_at, set_visit_count,
    set_want_to_go, upsert_user_profile, create_inquiry, list_inquiries,
)
from .import_csv import import_csv
from .zoo_features import get_feature
from .zoo_aliases import get_aliases
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse
from uuid import uuid4
from collections import deque




BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me")
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "").lower() in ("1", "true", "yes")
BUILD = os.getenv("BUILD", "dev")
# ★ローカル検証専用のテストログインを許可するか。
#   本番(render.yaml)ではこの環境変数を設定しない＝常に False。絶対に本番で有効化しないこと。
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "").lower() in ("1", "true", "yes")


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
            # ただし一括アップロードは「動物園1園＝1リクエスト」なので、
            # 複数園まとめて追加すると10回ではすぐ足りなくなる。別枠で少し緩める。
            if request.method == "POST" and path.endswith("/photos/bulk"):
                ok = _hit(f"upbulk:{ip}", limit=30, window_sec=60)  # 1分30回
            elif request.method == "POST" and "/photos" in path:
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
    # CSV_PATH を設定しておくと、起動のたびにCSVの内容を取り込む。
    #
    # 以前は「DBが空のときだけ」取り込む作りだったが、それだと本番の
    # 既存DBには園の追加も緯度経度の修正も一切反映されなかった。
    #
    # import_csv は upsert で、
    #   - 既存の行は (name, location_raw, url) が一致するものを更新する
    #   - CSVが空欄の項目で既存の値を上書きしない
    #   - CSVに無い園を消さない
    # ため、毎回走っても既存データを壊さない。
    csv_path = os.getenv("CSV_PATH", "")
    if csv_path:
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
    # ローカル検証時はGoogleの認証情報が無いので、テストログインへ回す。
    # ALLOW_DEV_LOGIN は本番(render.yaml)では未設定＝この分岐は本番では通らない。
    if ALLOW_DEV_LOGIN and not os.getenv("GOOGLE_CLIENT_ID"):
        return RedirectResponse(url="/dev-login")
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

@app.get("/dev-login")
def dev_login(request: Request):
    """ローカル検証用：Googleを使わずテストユーザーでログインする。
    ALLOW_DEV_LOGIN=1 のときだけ有効。本番では無効（404）。"""
    if not ALLOW_DEV_LOGIN:
        raise HTTPException(404, "Not found")
    uid = "google:dev-tester"
    request.session["user_id"] = uid
    request.session["email"] = "dev@example.com"
    request.session["name"] = "ローカルテスト"
    request.session["picture"] = None
    with session() as db:
        upsert_user_profile(db, uid, "dev@example.com", "ローカルテスト")
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


def _safe_visit_dates(v) -> list:
    """visit_dates は '["2024-03-15", ...]' の JSON文字列で入っている想定。
    万一壊れた値が入っていても、その1件のせいで一覧API全体が500にならないようにする。
    （水族館側と同じ対応）"""
    raw = getattr(v, "visit_dates", None) if v else None
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


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
                "aliases": get_aliases(z.name),
                "prefecture": z.prefecture,
                "city": z.city,
                "location_raw": z.location_raw,
                "url": z.url,
                "mola_star": z.mola_star,
                "twitter_id": z.twitter_id or "",
                "instagram_id": z.instagram_id or "",
                "visited": bool(v.visited) if v else False,
                "visited_at": v.visited_at.isoformat() if (v and v.visited_at) else None,
                "visit_count": v.visit_count if v else 0,
                "visit_dates": _safe_visit_dates(v),
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
            "aliases": get_aliases(z.name),
            "prefecture": z.prefecture,
            "city": z.city,
            "location_raw": z.location_raw,
            "url": z.url,
            "mola_star": z.mola_star,
            "twitter_id": z.twitter_id or "",
            "instagram_id": z.instagram_id or "",
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
    """静的ページ＋全動物園ページを含む sitemap を動的生成（園の追加に自動追従）。

    以前は web/sitemap.xml を返していたが、5ページしか載せておらず
    詳細ページが検索エンジンに伝わらなかったため、生成方式に変更した。"""
    base = "https://zoo-log.onrender.com"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    static_pages = [
        ("/", "weekly", "1.0"),
        ("/about/", "monthly", "0.8"),
        ("/missions/", "monthly", "0.6"),
        ("/updates/", "weekly", "0.5"),
        ("/contact/", "monthly", "0.4"),
        ("/privacy/", "yearly", "0.3"),
    ]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, pri in static_pages:
        parts.append(
            f"  <url><loc>{base}{loc}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>"
        )
    try:
        with session() as db:
            for z in list_zoos(db):
                parts.append(
                    f"  <url><loc>{base}/zoo/{z.id}</loc>"
                    f"<lastmod>{today}</lastmod><changefreq>monthly</changefreq>"
                    f"<priority>0.6</priority></url>"
                )
    except Exception:
        pass  # DB障害時でも静的ページ分は返す
    parts.append("</urlset>")
    return Response(content="\n".join(parts), media_type="application/xml")


# ===== 個別動物園ページ（SSR・SEO用）=====
# 水族館側の /aquarium/{id} と同じ作り。施設ごとに固有のHTMLを返すことで、
# 検索エンジンに園数ぶんの入口を持たせる。

def _esc(s):
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# models.py の has_* と対応。表示順もこの順。
_ZOO_ANIMALS = [
    ("has_elephant",   "🐘", "ゾウ"),
    ("has_giraffe",    "🦒", "キリン"),
    ("has_lion",       "🦁", "ライオン"),
    ("has_tiger",      "🐅", "トラ"),
    ("has_panda",      "🐼", "ジャイアントパンダ"),
    ("has_red_panda",  "🦝", "レッサーパンダ"),
    ("has_gorilla",    "🦍", "ゴリラ"),
    ("has_hippo",      "🦛", "カバ"),
    ("has_koala",      "🐨", "コアラ"),
    ("has_polar_bear", "🐻‍❄️", "ホッキョクグマ"),
]

# 動物アイコン（線画）。フロントと同じ web/creature-icons.js の JSON 部分を読む。
# 読めなかったときは上の絵文字で表示する（詳細ページ自体は必ず出す）
def _load_creature_svgs() -> dict:
    try:
        src = (WEB_DIR / "creature-icons.js").read_text(encoding="utf-8")
        body = src.split("/*CREATURES-JSON*/")[1].split("/*END*/")[0]
        return {c["key"]: c["svg"] for c in json.loads(body)}
    except Exception:
        traceback.print_exc()
        return {}

_CREATURE_SVGS = _load_creature_svgs()


def _creature_icon_html(key: str, emoji: str) -> str:
    svg = _CREATURE_SVGS.get(key)
    if not svg:
        return emoji + " "
    return f'<svg class="creature-icon" viewBox="0 0 24 24" aria-hidden="true">{svg}</svg>'


# 飼育している園が限られる動物（紹介文で見どころとして触れる）
_ZOO_RARE_ANIMALS = {"has_panda", "has_koala", "has_gorilla", "has_polar_bear"}

# 紹介文で使う地方区分（web/app.js の REGION_BY_PREF と同じ）
_REGION_BY_PREF = {
    "北海道": "北海道",
    "青森県": "東北", "岩手県": "東北", "宮城県": "東北",
    "秋田県": "東北", "山形県": "東北", "福島県": "東北",
    "茨城県": "関東", "栃木県": "関東", "群馬県": "関東", "埼玉県": "関東",
    "千葉県": "関東", "東京都": "関東", "神奈川県": "関東",
    "新潟県": "中部", "富山県": "中部", "石川県": "中部", "福井県": "中部",
    "山梨県": "中部", "長野県": "中部", "岐阜県": "中部", "静岡県": "中部", "愛知県": "中部",
    "三重県": "近畿", "滋賀県": "近畿", "京都府": "近畿", "大阪府": "近畿",
    "兵庫県": "近畿", "奈良県": "近畿", "和歌山県": "近畿",
    "鳥取県": "中国", "島根県": "中国", "岡山県": "中国", "広島県": "中国", "山口県": "中国",
    "徳島県": "四国", "香川県": "四国", "愛媛県": "四国", "高知県": "四国",
    "福岡県": "九州・沖縄", "佐賀県": "九州・沖縄", "長崎県": "九州・沖縄", "熊本県": "九州・沖縄",
    "大分県": "九州・沖縄", "宮崎県": "九州・沖縄", "鹿児島県": "九州・沖縄", "沖縄県": "九州・沖縄",
}

# ヘッダー／ドロワー／フッターは web/about/index.html などの静的ページと同じマークアップに揃えている。
# （静的ページ側を変えたら、こちらも同じ内容に更新すること）
ZOO_HEADER = """
  <header>
    <h1><a href="/" style="color:inherit;text-decoration:none;">全国動物園スタンプラリー</a></h1>
    <div class="userbox" id="userbox">
      <div class="bell-wrap">
        <button id="updateBellBtn" class="bell-btn" type="button" aria-haspopup="true" aria-expanded="false" aria-label="更新情報のお知らせ">
          <svg class="bell-icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>
          <span id="bellBadge" class="bell-badge" hidden></span>
        </button>
        <div id="bellPopover" class="bell-popover" hidden>
          <div class="bell-popover__title"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5.5h13a2 2 0 0 1 2 2V17a1.5 1.5 0 0 1-1.5 1.5H6A2 2 0 0 1 4 16.5v-11z"/><path d="M17 18.5A1.5 1.5 0 0 0 18.5 17V9h2v8a1.5 1.5 0 0 1-1.5 1.5"/><line x1="7" y1="9" x2="14" y2="9"/><line x1="7" y1="12" x2="14" y2="12"/><line x1="7" y1="15" x2="11" y2="15"/></svg>更新情報</div>
          <ul class="bell-popover__list">
            <li><span class="bell-popover__date">2026/10/07</span>動物のアイコンを新しいデザインにしました。</li>
            <li><span class="bell-popover__date">2026/09/25</span>動物園を12園追加し、全47都道府県をカバーしました（合計103園）。</li>
            <li><span class="bell-popover__date">2026/09/23</span>動物園ごとの詳細ページを追加しました。紹介・地図・会える動物をまとめて見られます。</li>
            
          </ul>
          <a href="/updates/" class="bell-popover__more">すべての更新情報を見る →</a>
        </div>
      </div>
      <button id="menuBtn" class="hamburger-btn" aria-label="メニューを開く"><svg class="hamburger-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg></button>
    </div>
  </header>
"""

ZOO_DRAWER = """
  <div id="drawerOverlay" class="drawer-overlay"></div>
  <nav id="drawer" class="drawer" aria-hidden="true">
    <button id="drawerClose" class="drawer-close" aria-label="閉じる">×</button>
    <div class="drawer__login">
      <span id="loginStatus" class="drawer-login-status"></span>
      <button class="linklike" id="googleLogin" type="button">Googleでログイン</button>
      <button class="linklike" id="logoutBtn" type="button" style="display:none;">ログアウト</button>
    </div>
    <div class="drawer__inner">
      <a href="/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11.5 12 4l8 7.5"/><path d="M6 10v9a1 1 0 0 0 1 1h3v-6h4v6h3a1 1 0 0 0 1-1v-9"/></svg>トップページ</a>
      <a href="/updates/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5.5h13a2 2 0 0 1 2 2V17a1.5 1.5 0 0 1-1.5 1.5H6A2 2 0 0 1 4 16.5v-11z"/><path d="M17 18.5A1.5 1.5 0 0 0 18.5 17V9h2v8a1.5 1.5 0 0 1-1.5 1.5"/><line x1="7" y1="9" x2="14" y2="9"/><line x1="7" y1="12" x2="14" y2="12"/><line x1="7" y1="15" x2="11" y2="15"/></svg>更新情報</a>
      <a href="/about/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><line x1="12" y1="11" x2="12" y2="16.5"/><circle cx="12" cy="7.8" r="0.9" fill="currentColor" stroke="none"/></svg>このアプリについて</a>
      <a href="/missions/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10v3.5a5 5 0 0 1-10 0V4z"/><path d="M7 5H4.5A2.5 2.5 0 0 0 7 9"/><path d="M17 5h2.5A2.5 2.5 0 0 1 17 9"/><path d="M12 12.5V16"/><path d="M9 20h6"/><path d="M9.5 16.5h5v3.5h-5z"/></svg>ミッション</a>
      <a id="galleryBtn" href="/gallery/" class="drawer-link" style="display:none;"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8.5h3.2L9 6h6l1.8 2.5H20v10H4v-10z"/><circle cx="12" cy="13" r="3.2"/></svg>マイ写真ギャラリー</a>
      <a href="/contact/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="M4 6.5l8 6.5 8-6.5"/></svg>お問い合わせ</a>
      <a href="/privacy/" class="drawer-link"><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="11" width="13" height="8.5" rx="2"/><path d="M8.5 11V7.8a3.5 3.5 0 0 1 7 0V11"/></svg>プライバシーポリシー</a>
      <hr class="drawer-divider">
      <div class="drawer-section-label">姉妹サイト</div>
      <a href="https://aquarium-log.onrender.com/" class="drawer-link" target="_blank" rel="noopener">🐟 全国水族館スタンプラリー</a>
    </div>
  </nav>
"""

ZOO_FOOTER = """
  <footer class="site-footer">
    <div class="footer-inner">
      <a href="/contact/">お問い合わせ</a>
      <a href="/privacy/">プライバシーポリシー</a>
      <a href="https://aquarium-log.onrender.com/" target="_blank" rel="noopener noreferrer">🐟 全国水族館スタンプラリー</a>
      <span>© 2025 全国動物園スタンプラリー</span>
    </div>
  </footer>
  <script src="/nav.js?v=20260923-1"></script>
"""


@app.get("/zoo/{zoo_id}", response_class=HTMLResponse, include_in_schema=False)
def zoo_page(zoo_id: int):
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        all_zoo = list_zoos(db)
        same_pref = [x for x in all_zoo
                     if x.prefecture == z.prefecture and x.id != z.id and not x.is_closed]
        same_pref_count = len(same_pref)
        related = same_pref[:8]
        try:
            visited_users = db.exec(
                select(func.count(func.distinct(Visit.user_id)))
                .where(Visit.zoo_id == zoo_id, Visit.visited == True)
            ).one()
            visited_users = int(visited_users or 0)
        except Exception:
            visited_users = 0

    base = "https://zoo-log.onrender.com"
    canonical = f"{base}/zoo/{z.id}"
    loc = (z.prefecture or "") + (z.city or "")

    animals = [(ic, lb) for key, ic, lb in _ZOO_ANIMALS if getattr(z, key, False)]
    animals_html = "".join(
        f'<span class="zoo-animal">{_creature_icon_html(key, ic)}{lb}</span>'
        for key, ic, lb in _ZOO_ANIMALS if getattr(z, key, False)
    )

    # 愛称（例:「のんほいパーク」）。検索エンジン向けに本文と説明文へ入れる
    aliases = get_aliases(z.name)
    alias_main = aliases[0] if aliases else ""

    # 紹介文（DBにある事実だけで組み立てる。営業時間や料金など未取得の情報は書かない）
    intro_parts = []

    region = _REGION_BY_PREF.get(z.prefecture or "", "")
    p1 = f"{_esc(z.name)}は、{_esc(loc) or '所在地不明の場所'}にある動物園です。"
    if alias_main:
        p1 += f"「{_esc(alias_main)}」の愛称で親しまれています。"
    if region and z.prefecture:
        # 「北海道エリア（北海道）」のような重複を避ける
        area = _esc(region) if region == z.prefecture else f"{_esc(region)}エリア（{_esc(z.prefecture)}）"
        p1 += f"{area}の動物園として、本サイトのスタンプラリーに登録されています。"
    if z.is_closed:
        closed_when = f"（{_esc(z.closed_at)}）" if z.closed_at else ""
        p1 += f"なお、この施設は現在閉園しています{closed_when}。訪問の記録は思い出として残せます。"
    intro_parts.append(p1)

    # 園ごとの特徴（app/zoo_features.py に手で書いてある分だけ）
    feature = get_feature(z.name)
    if feature:
        intro_parts.append(_esc(feature))

    if animals:
        names = "・".join(lb for ic, lb in animals[:6])
        if len(animals) == 1:
            # 1種類のときに「といった、全1種類の」は不自然なので言い切る
            p2 = f"園内では{names}に会えます。"
        else:
            p2 = f"園内では{names}"
            p2 += "など、" if len(animals) > 6 else "といった、"
            p2 += f"全{len(animals)}種類の動物に会えます。"
        rare = [lb for key, ic, lb in _ZOO_ANIMALS
                if getattr(z, key, False) and key in _ZOO_RARE_ANIMALS]
        if rare:
            p2 += f"なかでも{('・'.join(rare))}は、全国でも見られる園が限られる動物です。"
        intro_parts.append(p2)
    else:
        intro_parts.append(
            "会える動物のデータは現在準備中です。詳しい展示内容は公式サイトをご確認ください。"
        )

    p3 = []
    if z.url:
        p3.append("開園時間・料金・イベントなどの最新情報は公式サイトで確認できます")
    if z.twitter_id or z.instagram_id:
        p3.append("X・InstagramのSNSでは日々の動物の様子が発信されています")
    if z.lat is not None and z.lng is not None:
        p3.append("上の地図から現在地からのルートも調べられます")
    if p3:
        intro_parts.append("。".join(p3) + "。")

    p4 = "このアプリでは、訪問した日・訪問回数・写真・メモを動物園ごとに記録できます。"
    if same_pref_count:
        p4 += f"{_esc(z.prefecture)}には他にも{same_pref_count}か所の動物園が登録されているので、あわせてめぐってみてください。"
    intro_parts.append(p4)

    intro = "".join(f"<p>{s}</p>" for s in intro_parts)

    # リンク（公式・SNS）
    links = []
    if z.url:
        links.append(f'<a class="zoo-link" href="{_esc(z.url)}" target="_blank" rel="noopener noreferrer">公式サイト ↗</a>')
    if z.twitter_id:
        links.append(f'<a class="zoo-link" href="https://x.com/{_esc(z.twitter_id)}" target="_blank" rel="noopener noreferrer">X (旧Twitter)</a>')
    if z.instagram_id:
        links.append(f'<a class="zoo-link" href="https://www.instagram.com/{_esc(z.instagram_id)}/" target="_blank" rel="noopener noreferrer">Instagram</a>')
    links_html = "".join(links)

    # 地図（OpenStreetMap 埋め込み・JS不要）
    map_html = ""
    same_as = []
    if z.url: same_as.append(z.url)
    if z.twitter_id: same_as.append(f"https://x.com/{z.twitter_id}")
    if z.instagram_id: same_as.append(f"https://www.instagram.com/{z.instagram_id}/")
    if z.lat is not None and z.lng is not None:
        d = 0.02
        bbox = f"{z.lng-d}%2C{z.lat-d}%2C{z.lng+d}%2C{z.lat+d}"
        src = (f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}"
               f"&layer=mapnik&marker={z.lat}%2C{z.lng}")
        map_html = (f'<iframe class="zoo-map" src="{src}" loading="lazy" '
                    f'title="{_esc(z.name)}の地図"></iframe>'
                    f'<p class="zoo-maplink"><a href="https://www.openstreetmap.org/?mlat={z.lat}&mlon={z.lng}#map=15/{z.lat}/{z.lng}" '
                    f'target="_blank" rel="noopener noreferrer">大きな地図で見る ↗</a></p>')

    related_html = "".join(
        f'<li><a href="/zoo/{x.id}">{_esc(x.name)}</a></li>' for x in related
    ) or '<li>（同じ都道府県の他の動物園は登録されていません）</li>'

    closed_badge = ""
    if z.is_closed:
        closed_badge = f'<span class="zoo-closed">閉園{(" " + _esc(z.closed_at)) if z.closed_at else ""}</span>'

    visited_line = ""
    if visited_users > 0:
        visited_line = f'<p class="zoo-visited">このアプリで <b>{visited_users}</b> 人が訪問済みです。</p>'

    # 構造化データ
    ld_attraction = {
        "@context": "https://schema.org",
        "@type": "Zoo",
        "name": z.name,
        "url": canonical,
        "address": {
            "@type": "PostalAddress",
            "addressRegion": z.prefecture or "",
            "addressLocality": z.city or "",
            "addressCountry": "JP",
        },
    }
    if aliases:
        # 愛称を構造化データにも載せる（1件なら文字列、複数なら配列）
        ld_attraction["alternateName"] = aliases if len(aliases) > 1 else aliases[0]
    if z.lat is not None and z.lng is not None:
        ld_attraction["geo"] = {"@type": "GeoCoordinates", "latitude": z.lat, "longitude": z.lng}
    if same_as:
        ld_attraction["sameAs"] = same_as
    ld_breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "ホーム", "item": base + "/"},
            {"@type": "ListItem", "position": 2, "name": (z.prefecture or "") + "の動物園"},
            {"@type": "ListItem", "position": 3, "name": z.name, "item": canonical},
        ],
    }
    ld = (f'<script type="application/ld+json">{json.dumps(ld_attraction, ensure_ascii=False)}</script>'
          f'<script type="application/ld+json">{json.dumps(ld_breadcrumb, ensure_ascii=False)}</script>')

    alias_desc = f"愛称は{alias_main}。" if alias_main else ""
    desc = f"{z.name}（{loc}）の基本情報。{alias_desc}会える動物・公式サイト・地図・アクセス。全国動物園スタンプラリーで訪問記録・写真・メモを管理できます。"

    page = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(z.name)}（{_esc(z.prefecture)}）｜全国動物園スタンプラリー</title>
  <meta name="description" content="{_esc(desc)}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:title" content="{_esc(z.name)}（{_esc(z.prefecture)}）｜全国動物園スタンプラリー">
  <meta property="og:description" content="{_esc(desc)}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{canonical}">
  <meta property="og:site_name" content="全国動物園スタンプラリー">
  <meta name="twitter:card" content="summary">
  <meta name="theme-color" content="#2d6a4f">
  <link rel="icon" type="image/svg+xml" href="/icon.svg">
  {ld}
  <link rel="stylesheet" href="/styles.css?v=20261007-1">
  <style>
    /* ヘッダー常時固定（他ページと揃える） */
    header {{
      position: sticky !important;
      top: 0 !important;
      z-index: 500 !important;
      box-shadow: 0 2px 10px rgba(0,0,0,.12) !important;
    }}
    .zoo-wrap {{ max-width: 800px; margin: 18px auto 40px; padding: 0 16px; line-height: 1.85; }}
    .zoo-wrap h1 {{ color: #1b4332; }}
    .zoo-breadcrumb {{ font-size: 12px; color: #789; margin-bottom: 10px; }}
    .zoo-breadcrumb a {{ color: #2d6a4f; text-decoration: none; }}
    .zoo-loc {{ color: #456; font-size: 14px; margin: 2px 0 12px; }}
    .zoo-links {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }}
    .zoo-link {{ display: inline-block; background: #e8f3ec; color: #1b4332; border-radius: 999px;
               padding: 6px 14px; font-size: 13px; text-decoration: none; font-weight: 700; }}
    .zoo-map {{ width: 100%; height: 300px; border: 1px solid #d5e3d9; border-radius: 12px; }}
    .zoo-maplink {{ font-size: 12px; margin: 4px 0 0; }}
    .zoo-animals {{ display: flex; flex-wrap: wrap; gap: 7px; margin: 8px 0 4px; }}
    .zoo-animal {{ background: #f2f8f4; border: 1px solid #d5e3d9; border-radius: 999px;
                 padding: 4px 12px; font-size: 13.5px;
                 display: inline-flex; align-items: center; gap: 5px; }}
    .zoo-animal .creature-icon {{ color: #2d6a4f; }}
    .zoo-cta {{ display: block; width: 100%; text-align: center; background: #2d6a4f; color: #fff;
              padding: 13px; border-radius: 12px; font-weight: 700; text-decoration: none; margin: 22px 0;
              border: none; font-size: 15px; font-family: inherit; cursor: pointer; }}
    .zoo-cta:hover {{ background: #245840; }}
    .zoo-cta:disabled {{ opacity: .6; cursor: default; }}
    .zoo-cta.is-visited {{ background: #2a7d5a; }}
    .zoo-cta.is-visited:hover {{ background: #23684b; }}
    .zoo-done-box {{ max-width: 340px; }}
    .zoo-done-text {{ font-size: 14px; color: #456; margin: 0 0 4px; }}
    .zoo-want {{ display: block; width: 100%; margin: -8px 0 22px; padding: 12px;
               background: transparent; border: 1px solid #ccc; border-radius: 12px;
               font-size: 15px; font-family: inherit; font-weight: 700; color: #888;
               cursor: pointer; transition: background .15s, color .15s, border-color .15s; }}
    .zoo-want:hover:not(.active) {{ border-color: #bbb; background: rgba(0,0,0,.02); color: #666; }}
    .zoo-want.active {{ background: #fff8e6; border-color: #f6a623; color: #d4820a; }}
    .zoo-want:disabled {{ opacity: .6; cursor: default; }}
    .zoo-visited {{ color: #2a7d5a; font-size: 14px; }}
    .zoo-closed {{ background: #b00; color: #fff; font-size: 12px; border-radius: 6px; padding: 2px 8px; margin-left: 8px; }}
    .zoo-related li {{ margin: 2px 0; }}
    .zoo-related a {{ color: #2d6a4f; }}
  </style>
</head>
<body>
{ZOO_HEADER}
{ZOO_DRAWER}
  <div class="zoo-wrap">
    <nav class="zoo-breadcrumb"><a href="/">ホーム</a> ＞ {_esc(z.prefecture)}の動物園 ＞ {_esc(z.name)}</nav>

    <h1>{_esc(z.name)}{closed_badge}</h1>
    <p class="zoo-loc">📍 {_esc(loc) or "所在地不明"}</p>

    <div class="zoo-links">{links_html}</div>

    {map_html}

    <h2>この動物園で会える動物</h2>
    <div class="zoo-animals">{animals_html or "（動物データは準備中です）"}</div>

    <h2>{_esc(z.name)}について</h2>
    {intro}
    {visited_line}

    <button type="button" class="zoo-cta" id="zooVisitBtn" disabled>このアプリで訪問を記録する</button>
    <button type="button" class="zoo-want" id="zooWantBtn" hidden>行きたい☆</button>

    <h2>{_esc(z.prefecture)}の他の動物園</h2>
    <ul class="zoo-related">{related_html}</ul>

    <p style="margin-top:24px;"><a href="/">← 全国の動物園一覧に戻る</a></p>
  </div>

  <!-- 訪問を記録しました ポップアップ -->
  <div id="zooDoneModal" class="modal-overlay" style="display:none;" role="dialog" aria-modal="true">
    <div class="modal-box zoo-done-box">
      <p class="modal-title">✅ 訪問を記録しました</p>
      <p class="zoo-done-text" id="zooDoneText"></p>
      <div class="modal-actions">
        <button id="zooDoneTop" class="modal-btn modal-btn--primary">トップに戻る</button>
        <button id="zooDoneClose" class="modal-btn modal-btn--cancel">閉じる</button>
      </div>
    </div>
  </div>

  <!-- 訪問済を解除しますか？ ポップアップ -->
  <div id="zooUndoModal" class="modal-overlay" style="display:none;" role="dialog" aria-modal="true">
    <div class="modal-box zoo-done-box">
      <p class="modal-title">訪問済を解除しますか？</p>
      <p class="zoo-done-text">訪問日や訪問回数がリセットされます。</p>
      <div class="modal-actions">
        <button id="zooUndoOk" class="modal-btn modal-btn--secondary">解除する</button>
        <button id="zooUndoCancel" class="modal-btn modal-btn--cancel">やめる</button>
      </div>
    </div>
  </div>

  <script>
  (function () {{
    var ZOO_ID = {z.id};
    var ZOO_NAME = {json.dumps(z.name, ensure_ascii=False)};
    var ZOO_CLOSED = {json.dumps(bool(z.is_closed))};
    var btn = document.getElementById('zooVisitBtn');
    var wantBtn = document.getElementById('zooWantBtn');
    var doneModal = document.getElementById('zooDoneModal');
    var doneText = document.getElementById('zooDoneText');
    var undoModal = document.getElementById('zooUndoModal');
    var loggedIn = false, visited = false, wantToGo = false, csrf = '';

    function paint() {{
      if (!loggedIn) {{
        btn.textContent = 'ログインして訪問を記録する';
        btn.classList.remove('is-visited');
      }} else {{
        btn.textContent = visited ? '訪問済✅（解除）' : 'このアプリで訪問を記録する';
        btn.classList.toggle('is-visited', visited);
      }}
      btn.disabled = false;

      var showWant = loggedIn && !ZOO_CLOSED;
      wantBtn.hidden = !showWant;
      if (showWant) {{
        wantBtn.textContent = wantToGo ? '行きたい★' : '行きたい☆';
        wantBtn.classList.toggle('active', wantToGo);
        wantBtn.disabled = false;
      }}
    }}

    function showDone() {{
      doneText.textContent = ZOO_NAME + ' を訪問済にしました。';
      doneModal.style.display = '';
    }}
    function hide(m) {{ m.style.display = 'none'; }}

    document.getElementById('zooDoneTop').onclick = function () {{ location.href = '/'; }};
    document.getElementById('zooDoneClose').onclick = function () {{ hide(doneModal); }};
    doneModal.onclick = function (e) {{ if (e.target === doneModal) hide(doneModal); }};
    document.getElementById('zooUndoCancel').onclick = function () {{ hide(undoModal); }};
    undoModal.onclick = function (e) {{ if (e.target === undoModal) hide(undoModal); }};

    function setVisited(next) {{
      btn.disabled = true;
      return fetch('/api/zoos/' + ZOO_ID + '/visited', {{
        method: 'PUT',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }},
        body: JSON.stringify({{ visited: next }})
      }}).then(function (r) {{
        if (!r.ok) throw new Error('failed');
        visited = next;
        paint();
        if (next) showDone();
      }}).catch(function () {{
        paint();
        alert('通信に失敗しました。時間をおいて試してください。');
      }});
    }}

    document.getElementById('zooUndoOk').onclick = function () {{
      hide(undoModal);
      setVisited(false);
    }};

    btn.onclick = function () {{
      if (!loggedIn) {{ location.href = '/login'; return; }}
      if (visited) {{ undoModal.style.display = ''; return; }}
      setVisited(true);
    }};

    wantBtn.onclick = function () {{
      if (wantBtn.disabled) return;
      wantBtn.disabled = true;
      var next = !wantToGo;
      wantToGo = next;
      paint();
      fetch('/api/zoos/' + ZOO_ID + '/want_to_go', {{
        method: 'PUT',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }},
        body: JSON.stringify({{ want_to_go: next }})
      }}).then(function (r) {{
        if (!r.ok) throw new Error('failed');
      }}).catch(function () {{
        wantToGo = !next;
        paint();
        alert('通信に失敗しました。時間をおいて試してください。');
      }}).then(function () {{ wantBtn.disabled = false; }});
    }};

    // 初期状態の取得（ログイン状態・訪問済みかどうか・CSRFトークン）
    fetch('/api/me', {{ credentials: 'same-origin' }})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(function (me) {{
        loggedIn = !!(me && me.user_id);
        paint();
        if (!loggedIn) return null;
        return Promise.all([
          fetch('/api/csrf', {{ credentials: 'same-origin' }}).then(function (r) {{ return r.json(); }}),
          fetch('/api/zoos', {{ credentials: 'same-origin' }}).then(function (r) {{ return r.json(); }})
        ]).then(function (res) {{
          csrf = (res[0] && res[0].token) || '';
          var mine = (res[1] || []).filter(function (x) {{ return x.id === ZOO_ID; }})[0];
          visited = !!(mine && mine.visited);
          wantToGo = !!(mine && mine.want_to_go);
          paint();
        }});
      }})
      .catch(function () {{ paint(); }});
  }})();
  </script>
{ZOO_FOOTER}
</body>
</html>"""
    return HTMLResponse(content=page)

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


# ===== 写真のまとめて追加 =====

BULK_MAX_FILES = 20   # 1リクエストあたりの上限


def _is_image_bytes(b: bytes) -> bool:
    if b.startswith(b"\xff\xd8\xff"):                      # JPEG
        return True
    if b.startswith(b"\x89PNG\r\n\x1a\n"):                 # PNG
        return True
    if b.startswith(b"RIFF") and b[8:12] == b"WEBP":       # WEBP
        return True
    return False


@app.post("/api/zoos/{zoo_id}/photos/bulk")
async def upload_photos_bulk(zoo_id: int, request: Request,
                             files: List[UploadFile] = File(...)):
    """1つの動物園に複数枚まとめてアップロードする。

    1枚ずつ POST するとレート制限（写真は1分10回）にすぐ当たるので、
    まとめて1リクエストで受け取る。失敗したファイルはスキップして結果に含める。
    """
    uid = get_user_id(request)

    with session() as db:
        if not db.get(Zoo, zoo_id):
            raise HTTPException(404, "Zoo not found")

    if not files:
        raise HTTPException(400, "No files")
    if len(files) > BULK_MAX_FILES:
        raise HTTPException(400, f"Too many files (max {BULK_MAX_FILES})")

    safe_uid = uid.replace(":", "_")
    rel_dir = os.path.join(safe_uid, str(zoo_id))
    abs_dir = os.path.join(UPLOAD_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    MAX_BYTES = 5 * 1024 * 1024
    saved, errors = [], []

    for f in files:
        name = f.filename or "(no name)"
        try:
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"

            data = await f.read()
            if len(data) > MAX_BYTES:
                errors.append({"name": name, "reason": "5MBを超えています"})
                continue
            if not _is_image_bytes(data):
                errors.append({"name": name, "reason": "画像ファイルではありません"})
                continue

            fname = f"{uuid4().hex}{ext}"
            with open(os.path.join(abs_dir, fname), "wb") as out:
                out.write(data)

            rel_path = os.path.join(rel_dir, fname).replace("\\", "/")
            with session() as db:
                p = Photo(user_id=uid, zoo_id=zoo_id, path=rel_path)
                db.add(p)
                db.commit()
                db.refresh(p)
            saved.append({"id": p.id, "url": "/uploads/" + p.path})
        except Exception:
            # 1枚の失敗で全体を止めない（残りは保存する）
            errors.append({"name": name, "reason": "保存に失敗しました"})

    return {"saved": len(saved), "photos": saved, "errors": errors}


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
                "name":          u.name,
                "login_count":   u.login_count,
                "created_at":    u.created_at.isoformat(),
                "last_login_at": u.last_login_at.isoformat(),
                "visited_count": visit_map.get(u.user_id, 0),
                "want_count":    want_map.get(u.user_id, 0),
                "photo_count":   photo_map.get(u.user_id, 0),
            }
            for u in users
        ]


@app.get("/api/admin/photos")
def get_admin_photos(request: Request, limit: int = 100):
    require_admin(request)
    with session() as db:
        # UserProfile, Zoo と JOIN して直近の写真を limit 件取得
        rows = db.exec(
            select(Photo, UserProfile.name, Zoo.name)
            .join(UserProfile, Photo.user_id == UserProfile.user_id, isouter=True)
            .join(Zoo, Photo.zoo_id == Zoo.id, isouter=True)
            .order_by(Photo.created_at.desc())
            .limit(limit)
        ).all()

        return [
            {
                "id": p.id,
                "path": "/uploads/" + p.path,
                "created_at": p.created_at.isoformat(),
                "user_name": u_name or "不明なユーザー",
                "zoo_name": z_name or "不明な動物園",
            }
            for p, u_name, z_name in rows
        ]


class ZooSocialIn(BaseModel):
    twitter_id: Optional[str] = None
    instagram_id: Optional[str] = None


@app.put("/api/admin/zoos/{zoo_id}/social")
def update_zoo_social(zoo_id: int, body: ZooSocialIn, request: Request):
    """管理者が動物園のSNSアカウントIDを更新する。"""
    require_admin(request)
    with session() as db:
        z = db.get(Zoo, zoo_id)
        if not z:
            raise HTTPException(404, "Zoo not found")
        # 空文字はNullに統一
        z.twitter_id = body.twitter_id.strip() if body.twitter_id and body.twitter_id.strip() else None
        z.instagram_id = body.instagram_id.strip() if body.instagram_id and body.instagram_id.strip() else None
        db.add(z)
        db.commit()
        db.refresh(z)
    return {
        "id": z.id,
        "twitter_id": z.twitter_id or "",
        "instagram_id": z.instagram_id or "",
    }


# ===== X共有カード（OGP共有ページ） =====
SHARE_DIR_NAME = "_share"   # UPLOAD_DIR 配下のサブディレクトリ
SHARE_KEEP = 3              # ユーザーごとに保持する共有画像の最大数


@app.post("/api/share")
async def create_share(request: Request, file: UploadFile = File(...),
                       visited: int = 0, total: int = 0):
    """canvasで生成した成績画像を受け取り保存。共有ページURLを返す（ログイン＋CSRF必須）"""
    uid = get_user_id(request)

    data = await file.read()
    # 5MB制限
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 5MB)")
    # PNGマジックバイト確認
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Invalid image file (PNG only)")

    token = uuid4().hex
    rel_dir = SHARE_DIR_NAME
    abs_dir = os.path.join(UPLOAD_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    rel_path = f"{rel_dir}/{token}.png"
    abs_path = os.path.join(UPLOAD_DIR, rel_path)
    with open(abs_path, "wb") as f:
        f.write(data)

    with session() as db:
        sc = ShareCard(token=token, image_path=rel_path, user_id=uid,
                       visited=int(visited or 0), total=int(total or 0))
        db.add(sc)
        db.commit()

        # ユーザーごとに最新 SHARE_KEEP 件だけ残し、古い共有画像を削除
        old = db.exec(
            select(ShareCard)
            .where(ShareCard.user_id == uid)
            .order_by(ShareCard.created_at.desc())
        ).all()
        for s in old[SHARE_KEEP:]:
            old_abs = os.path.join(UPLOAD_DIR, s.image_path)
            try:
                if os.path.exists(old_abs):
                    os.remove(old_abs)
            except Exception:
                pass
            db.delete(s)
        db.commit()

    return {"token": token, "url": f"/share/{token}"}


@app.get("/share/{token}", response_class=HTMLResponse, include_in_schema=False)
def share_page(token: str, request: Request):
    """Xクローラ向けに og:image 付きHTMLを返す公開ページ（認証不要）"""
    # tokenを安全な文字に限定（パストラバーサル防止）
    if not token.isalnum():
        raise HTTPException(404, "Not found")
    with session() as db:
        sc = db.get(ShareCard, token)
    if not sc:
        raise HTTPException(404, "Not found")

    img_url = f"{BASE_URL}/uploads/{sc.image_path}"
    pct = round(sc.visited / sc.total * 100) if sc.total else 0
    title = f"{sc.visited}園訪問達成！（全{sc.total}園中 {pct}%） | 全国動物園スタンプラリー"
    desc = "全国の動物園をスタンプラリー形式で記録できるアプリ。あなたも動物園巡りの成績をシェアしよう！"
    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{img_url}">
<meta property="og:url" content="{BASE_URL}/share/{token}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{img_url}">
<style>
  body {{ margin:0; font-family:sans-serif; background:#1b4332; color:#fff;
         display:flex; flex-direction:column; align-items:center; padding:24px; }}
  img {{ max-width:100%; width:600px; border-radius:12px; box-shadow:0 8px 24px rgba(0,0,0,.4); }}
  a.btn {{ margin-top:20px; background:#2d6a4f; color:#fff; text-decoration:none;
          padding:12px 28px; border-radius:999px; font-weight:bold; }}
</style>
</head>
<body>
  <img src="/uploads/{sc.image_path}" alt="{title}">
  <a class="btn" href="/">🐘 全国動物園スタンプラリーを見る</a>
</body>
</html>"""
    return HTMLResponse(content=html)


    # 静的フロント（webディレクトリを確実に参照）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
