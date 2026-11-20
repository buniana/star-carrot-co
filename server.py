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
PRIVATE_PREFIXES = ("/saves", "/.git", "/server.py", "/.claude")


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
            if self.path.split("?", 1)[0].startswith(PRIVATE_PREFIXES):
                return self._json(404, {"error": "not found"})
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

    def do_POST(self):
        route, slot = self._route()
        if route != "save":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"error": "bad size"})
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
