import csv
from pathlib import Path
from sqlmodel import select
from .db import session, init_db
from .models import Zoo

FIELDS = [
    "name",
    "prefecture",
    "city",
    "location_raw",
    "url",
    "mola_star",
    "lat",
    "lng",
    "has_elephant",
    "has_giraffe",
    "has_lion",
    "has_tiger",
    "has_panda",
    "has_gorilla",
    "has_hippo",
    "has_koala",
    "has_polar_bear",
    "has_red_panda",
    "is_closed",
    "closed_at",
]

BOOL_FIELDS = {
    "has_elephant", "has_giraffe", "has_lion", "has_tiger", "has_panda",
    "has_gorilla", "has_hippo", "has_koala", "has_polar_bear", "has_red_panda",
    "is_closed"
}


def import_csv(csv_path: str) -> int:
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(csv_path)

    # Excel対策で utf-8-sig を優先
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ["name"] if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV missing columns: {missing}")

        inserted = 0
        with session() as db:
            for row in reader:
                data = {k: (row.get(k, "") or "").strip() for k in FIELDS if k in row}
                if not data.get("name"):
                    continue
                # mola_star は int 化
                try:
                    data["mola_star"] = int(data.get("mola_star") or 0)
                except Exception:
                    data["mola_star"] = 0
                    # lat/lng を float 化
                try:
                    data["lat"] = float(data.get("lat")) if data.get("lat") else None
                except Exception:
                    data["lat"] = None

                try:
                    data["lng"] = float(data.get("lng")) if data.get("lng") else None
                except Exception:
                    data["lng"] = None

                # bool 変換（"TRUE"/"true" → True, それ以外 → False）
                for bf in BOOL_FIELDS:
                    if bf in data:
                        data[bf] = str(data[bf]).strip().lower() in ("true", "1", "yes")

                # 既存チェック（uqに合わせる）
                exists = db.exec(
                    select(Zoo).where(
                        Zoo.name == data["name"],
                        Zoo.location_raw == data.get("location_raw", ""),
                        Zoo.url == data.get("url", ""),
                    )
                ).first()
                if exists:
                    for k, v in data.items():
                        if v is None and getattr(exists, k, None) is not None:
                            continue  # 既存の値をNoneで上書きしない
                        setattr(exists, k, v)
                    db.add(exists)
                else:
                    db.add(Zoo(**data))
                    inserted += 1

            db.commit()

        return inserted

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    args = ap.parse_args()
    init_db()
    n = import_csv(args.csv_path)
    print(f"imported: {n}")
