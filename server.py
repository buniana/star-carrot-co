#!/usr/bin/env python3
"""星际萝卜公司 · 小后端（只用 Python 标准库）

    python3 server.py            # 默认 http://localhost:8765
    python3 server.py --port 9000

功能：
  - 提供 index.html 等静态文件
  - GET    /api/health              健康检查
  - GET    /api/save/<slot>         读取存档  -> {"save": {...} | null}
  - POST   /api/save/<slot>         写入存档（JSON 对象，≤ 512KB）
  - DELETE /api/save/<slot>         删除存档
  - GET    /api/leaderboard         银河排行榜（按累计收入排序，前 10）
存档保存在 ./saves/<slot>.json
"""
import argparse
import json
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(ROOT, "saves")
SLOT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_BODY = 512 * 1024
LOCK = threading.Lock()
PRIVATE_NAMES = {"saves", "server.py", "__pycache__"}  # plus every dotfile / dot-directory
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def save_path(slot):
    return os.path.join(SAVE_DIR, slot + ".json")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # ---------- helpers ----------
    def _json(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _route(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return None, None
        parts = path[5:].strip("/").split("/")
        if parts == ["health"]:
            return "health", None
        if parts == ["leaderboard"]:
            return "leaderboard", None
        if len(parts) == 2 and parts[0] == "save" and SLOT_RE.match(parts[1]):
            return "save", parts[1]
        return "bad", None

    # ---------- verbs ----------
    def do_GET(self):
        route, slot = self._route()
        if route is None:
            if self._is_private():
                return self._json(404, {"error": "not found"})
            if self._serve_range():
                return None
            return super().do_GET()
        if route == "health":
            return self._json(200, {"ok": True})
        if route == "leaderboard":
            return self._json(200, {"top": self._leaderboard()})
        if route == "save":
            try:
                with LOCK, open(save_path(slot), encoding="utf-8") as f:
                    return self._json(200, {"save": json.load(f)})
            except FileNotFoundError:
                return self._json(200, {"save": None})
            except (OSError, ValueError):
                return self._json(500, {"error": "corrupt save"})
        return self._json(404, {"error": "not found"})

    def do_HEAD(self):
        if self._route()[0] is not None or self._is_private():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return super().do_HEAD()

    # ---------- static-file guards ----------
    def _is_private(self):
        """Resolve the request the same way the static handler will (URL-decoding,
        dropping '.'/'..', following symlinks) and refuse anything that lands
        outside ROOT, on a dotfile, or on a private name — so /%73aves/x.json,
        /./saves, /%2egit or /assets/../server.py can't slip past."""
        root = os.path.realpath(ROOT)
        target = os.path.realpath(self.translate_path(self.path))
        if target != root and not target.startswith(root + os.sep):
            return True
        rel = os.path.relpath(target, root)
        if rel == ".":
            return False
        parts = rel.split(os.sep)
        return any(p.startswith(".") for p in parts) or parts[0].lower() in PRIVATE_NAMES

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def _serve_range(self):
        """Answer a single 'Range: bytes=a-b' request with 206 (Safari needs this to play video)."""
        rng = self.headers.get("Range")
        if not rng:
            return False
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return False
        m = RANGE_RE.match(rng.strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return False  # multi-range or malformed: fall back to the full file
        size = os.path.getsize(path)
        if m.group(1) == "":
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(64 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                left -= len(chunk)
        return True

    def do_POST(self):
        route, slot = self._route()
        if route != "save":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0:
            return self._json(400, {"error": "empty body"})
        if n > MAX_BODY:
            return self._json(413, {"error": "too large"})
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "bad json"})
        if not isinstance(data, dict):
            return self._json(400, {"error": "save must be an object"})
        os.makedirs(SAVE_DIR, exist_ok=True)
        tmp = save_path(slot) + ".tmp"
        with LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, save_path(slot))
        return self._json(200, {"ok": True})

    def do_DELETE(self):
        route, slot = self._route()
        if route != "save":
            return self._json(404, {"error": "not found"})
        with LOCK:
            try:
                os.remove(save_path(slot))
            except FileNotFoundError:
                pass
        return self._json(200, {"ok": True})

    # ---------- leaderboard ----------
    def _leaderboard(self):
        rows = []
        if not os.path.isdir(SAVE_DIR):
            return rows
        with LOCK:
            for name in os.listdir(SAVE_DIR):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(SAVE_DIR, name), encoding="utf-8") as f:
                        s = json.load(f)
                    inv = s.get("inv") or {}
                    base = name[:-5].rsplit("-", 1)[0]
                    rows.append({
                        "id": base,
                        "planet": str(s.get("planet") or "无名星球")[:12],
                        "earned": int(inv.get("earned") or 0),
                        "hearts": int(inv.get("hearts") or 0),
                        "unlocks": sum(1 for v in (s.get("up") or {}).values() if v),
                    })
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
        # each player has an auto and a manual slot: keep their best one
        best = {}
        for r in rows:
            if r["id"] not in best or r["earned"] > best[r["id"]]["earned"]:
                best[r["id"]] = r
        rows = sorted(best.values(), key=lambda r: (-r["earned"], -r["hearts"]))
        for r in rows:
            r.pop("id", None)
        return rows[:10]

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Star Carrot Co. backend")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"🥕 星际萝卜公司 running at http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
