import time
import json
import csv
import os

IN_CSV     = "zoos_with_animals.csv"
OUT_CSV    = "zoos_final.csv"
CACHE_JSON = "geocode_cache.json"

# Nominatim(OSM) — 利用規約上、User-Agent必須 & 連続叩きすぎNG
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "zoo-stamp-app/1.0 (local script)"
}

try:
    import requests
except ImportError:
    raise SystemExit("requests がインストールされていません: pip install requests")


def load_cache():
    try:
        with open(CACHE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(cache):
    with open(CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# 検索の邪魔になる法人格の接頭辞。付いたままだと地図データに一致しない
# （例:「地方独立行政法人天王寺動物園」→ ヒットしない / 「天王寺動物園」→ ヒットする）
NOISE_PREFIXES = [
    "地方独立行政法人", "公益財団法人", "一般財団法人", "公益社団法人",
    "一般社団法人", "株式会社", "有限会社", "特定非営利活動法人",
]


def strip_noise(name: str) -> str:
    out = name
    for n in NOISE_PREFIXES:
        out = out.replace(n, "")
    return out.strip()


def _pref_matches(hit: dict, pref: str) -> bool:
    """取得した座標が期待した都道府県のものか確認する。

    過去に「東京都立大島公園」が京都の座標になった事故があったため、
    県が一致しない結果は採用しない。"""
    if not pref:
        return True
    disp = hit.get("display_name", "") or ""
    addr = hit.get("address", {}) or {}
    state = addr.get("province") or addr.get("state") or ""
    base = pref.replace("県", "").replace("府", "").replace("都", "")
    return (pref in disp) or (base in disp) or (base in state)


def geocode(query: str, pref: str = ""):
    """pref を渡すと、その都道府県に入っている結果だけを返す。"""
    params = {
        "q": query, "format": "jsonv2", "limit": 3,
        "addressdetails": 1, "countrycodes": "jp",
    }
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    for hit in data:
        if _pref_matches(hit, pref):
            return float(hit["lat"]), float(hit["lon"])
    return None


def main():
    # CSV読み込み
    with open(IN_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # lat/lng 列が無ければ追加
    if "lat" not in fieldnames:
        fieldnames = list(fieldnames) + ["lat", "lng"]
    for row in rows:
        row.setdefault("lat", "")
        row.setdefault("lng", "")

    cache = load_cache()
    updated = 0
    failed  = 0

    for i, row in enumerate(rows):
        # 既に lat/lng があればスキップ
        if row.get("lat") and row.get("lng"):
            continue

        pref = row["prefecture"]
        clean = strip_noise(row["name"])
        q = f'{pref} {row["city"]} {clean}'.strip()

        if q in cache:
            row["lat"], row["lng"] = cache[q]
            print(f"  [cache] {q}")
            continue

        try:
            # 市区町村つき → 県のみ → 施設名のみ の順に試す
            attempts = [
                q,
                f'{pref} {clean}'.strip(),
                clean,
            ]
            res = None
            for a in attempts:
                res = geocode(a, pref)
                if res:
                    break
                time.sleep(1.1)
            if res is None:
                print(f"  [FAILED] {q}")
                failed += 1
                time.sleep(1.1)
                continue
            lat, lng = res
            row["lat"] = lat
            row["lng"] = lng
            cache[q] = [lat, lng]
            updated += 1
            print(f"  [OK] {q} -> {lat:.4f}, {lng:.4f}")
        except Exception as e:
            print(f"  [ERROR] {q}: {e}")
            failed += 1

        # レート制限対策（重要）：最低1秒は空ける
        time.sleep(1.1)

        # 10件ごとに中間保存（中断しても再開できる）
        if (i + 1) % 10 == 0:
            save_cache(cache)
            _write_csv(OUT_CSV, fieldnames, rows)
            print(f"--- progress: {i+1}/{len(rows)}  updated={updated}  failed={failed} ---")

    save_cache(cache)
    _write_csv(OUT_CSV, fieldnames, rows)
    print(f"\nDONE  updated={updated}  failed={failed}")
    print(f"出力: {os.path.abspath(OUT_CSV)}")


def _write_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
