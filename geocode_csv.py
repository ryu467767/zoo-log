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


def geocode(query: str):
    params = {"q": query, "format": "jsonv2", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


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

        q = f'{row["prefecture"]} {row["city"]} {row["name"]}'.strip()

        if q in cache:
            row["lat"], row["lng"] = cache[q]
            print(f"  [cache] {q}")
            continue

        try:
            res = geocode(q)
            if res is None:
                # フォールバック：市区町村を省いて再検索
                q2 = f'{row["prefecture"]} {row["name"]}'.strip()
                res = geocode(q2)
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
