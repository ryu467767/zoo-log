"""写真の個人情報（撮影場所のGPS・撮影日時・機種など）を取り除く。

スマホで撮った写真には、撮影した場所の緯度経度（自宅の位置など）が
EXIF という形で埋め込まれていることがある。アップロード時にこれを消しておく。

- JPEG: EXIF / XMP / コメントを消す。ただし写真の向き（Orientation）だけは残す
        （消すと縦向きの写真が横に倒れて表示されるため）
- PNG : eXIf / テキスト（tEXt・iTXt・zTXt）を消す
- WebP: EXIF / XMP を消す

画素データには一切手を付けないので、画質は変わらない。
外部ライブラリは使わない（本番の requirements を増やさないため）。
"""
import os
import struct

# JPEG のうち消すセグメント：APP1(EXIF/XMP), APP12〜APP15, COM(コメント)
_JPEG_DROP = {0xE1, 0xEC, 0xED, 0xEE, 0xEF, 0xFE}


def _jpeg_orientation(app1: bytes):
    """APP1(EXIF) の中身から Orientation(0x0112) を読む。無ければ None"""
    if not app1.startswith(b"Exif\x00\x00"):
        return None
    t = app1[6:]
    if len(t) < 8:
        return None
    if t[:2] == b"II":
        e = "<"
    elif t[:2] == b"MM":
        e = ">"
    else:
        return None
    try:
        ifd = struct.unpack(e + "I", t[4:8])[0]
        n = struct.unpack(e + "H", t[ifd:ifd + 2])[0]
        for i in range(n):
            ent = t[ifd + 2 + i * 12: ifd + 14 + i * 12]
            tag, typ, cnt = struct.unpack(e + "HHI", ent[:8])
            if tag == 0x0112 and typ == 3:
                v = struct.unpack(e + "H", ent[8:10])[0]
                return v if 1 <= v <= 8 else None
    except Exception:
        return None
    return None


def _jpeg_orientation_segment(value: int) -> bytes:
    """Orientation だけを持つ最小の EXIF(APP1) を作る"""
    tiff = (b"MM\x00\x2a" + struct.pack(">I", 8)          # ヘッダ・IFD0の位置
            + struct.pack(">H", 1)                          # エントリ数
            + struct.pack(">HHIHH", 0x0112, 3, 1, value, 0)  # Orientation
            + struct.pack(">I", 0))                         # 次のIFDなし
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        return data
    out = [b"\xff\xd8"]
    i = 2
    orientation = None
    inserted = False
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            return data  # 想定外の形式はそのまま（壊さない）
        marker = data[i + 1]
        if marker == 0xD9:                      # EOI
            out.append(data[i:])
            break
        if marker == 0xDA:                      # SOS 以降は画素データ。そのまま全部残す
            if orientation and not inserted:
                out.insert(1, _jpeg_orientation_segment(orientation))
            out.append(data[i:])
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.append(data[i:i + 2])
            i += 2
            continue
        if i + 4 > n:
            return data
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i:i + 2 + seglen]
        if marker in _JPEG_DROP:
            if marker == 0xE1 and orientation is None:
                orientation = _jpeg_orientation(data[i + 4:i + 2 + seglen])
        else:
            out.append(seg)
        i += 2 + seglen
    else:
        return data
    return b"".join(out)


_PNG_DROP = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"}


def _strip_png(data: bytes) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        return data
    out = [sig]
    i = 8
    n = len(data)
    while i + 8 <= n:
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        chunk = data[i:i + 12 + length]
        if len(chunk) < 12 + length:
            return data
        if ctype not in _PNG_DROP:
            out.append(chunk)
        i += 12 + length
        if ctype == b"IEND":
            break
    return b"".join(out)


def _strip_webp(data: bytes) -> bytes:
    if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return data
    out = []
    i = 12
    n = len(data)
    while i + 8 <= n:
        ctype = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        total = 8 + size + (size & 1)
        chunk = data[i:i + total]
        if len(chunk) < 8 + size:
            return data
        if ctype not in (b"EXIF", b"XMP "):
            if ctype == b"VP8X":
                # 「EXIF/XMPあり」の印を外す
                flags = chunk[8] & ~0x0C
                chunk = chunk[:8] + bytes([flags]) + chunk[9:]
            out.append(chunk)
        i += total
    body = b"WEBP" + b"".join(out)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def strip_metadata(data: bytes) -> bytes:
    """画像のバイト列から個人情報になりうるメタデータを消して返す。
    うまく処理できない形式のときは、元のデータをそのまま返す（写真を壊さない）。"""
    try:
        if data.startswith(b"\xff\xd8"):
            return _strip_jpeg(data)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return _strip_png(data)
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return _strip_webp(data)
    except Exception:
        pass
    return data


def strip_existing_uploads(upload_dir: str, skip_dirs=("_share",)) -> int:
    """すでに保存されている写真からも、メタデータを消す（1回だけ実行する）。
    実行済みの印として upload_dir に .metadata_stripped_v1 を置く。戻り値は書き換えた枚数。"""
    marker = os.path.join(upload_dir, ".metadata_stripped_v1")
    if os.path.exists(marker):
        return 0
    changed = 0
    for root, dirs, files in os.walk(upload_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, "rb") as f:
                    data = f.read()
                cleaned = strip_metadata(data)
                if cleaned != data:
                    tmp = p + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(cleaned)
                    os.replace(tmp, p)
                    changed += 1
            except Exception:
                continue
    try:
        with open(marker, "w") as f:
            f.write(str(changed))
    except Exception:
        pass
    return changed
