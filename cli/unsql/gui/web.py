"""
unsql/gui/web.py
----------------
Zero-dependency local dashboard for the last SELECT result.
Uses only the stdlib http.server — no Flask, no FastAPI.
"""
from __future__ import annotations

import csv
import html
import io
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_current_data: dict = {"columns": [], "rows": [], "sql": "", "title": "UNSQL Results"}
_current_theme: str = "light"
_current_max: int = 10000
_server: HTTPServer | None = None
_thread: threading.Thread | None = None
_server_lock = threading.Lock()


def _h(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _sanitize(v: Any) -> str:
    s = str(v) if v is not None else ""
    stripped = s.lstrip(" \t\r\n")
    if stripped and stripped[:1] in ("=", "+", "-", "@", "|", "%", "\t", "\r", "\n"):
        return "'" + s
    if "\n=" in s or "\r=" in s or "\n+" in s or "\n@" in s:
        return "'" + s
    return s


def _render_html(
    columns: list[str],
    rows: list[Any],
    sql: str = "",
    title: str = "UNSQL Results",
    theme: str = "light",
    max_rows: int = 10000,
    nonce: str | None = None,
) -> str:
    display_rows = rows[:max_rows]
    truncated = len(rows) > max_rows

    col_headers = "".join(f"<th>{_h(c)}</th>" for c in columns)
    parts: list[str] = []
    for r in display_rows:
        if isinstance(r, dict):
            vals = [r.get(c, "") for c in columns]
        else:
            vals = list(r)
        cells = "".join(f"<td>{_h(v if v is not None else '')}</td>" for v in vals)
        parts.append(f"<tr>{cells}</tr>")
    row_html = "".join(parts)

    na = f' nonce="{nonce}"' if nonce else ""
    trunc_badge = f'<span class="badge">… truncated to {max_rows}</span>' if truncated else ""
    sql_block = f'<div class="sql"><pre>{_h(sql)}</pre></div>' if sql else ""

    css = """
:root{--bg:#ffffff;--fg:#111;--bd:#ddd;--th:#f6f6f6;--row-alt:#fafafa;--accent:#2563eb}
[data-theme="dark"]{--bg:#111;--fg:#eee;--bd:#333;--th:#1b1b1b;--row-alt:#161616;--accent:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:14px 18px;border-bottom:1px solid var(--bd)}
header h1{font-size:16px;margin:0}
.badge{font-size:12px;color:var(--accent);border:1px solid var(--bd);border-radius:10px;padding:1px 8px}
.spacer{flex:1}
button,.btn{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 12px;text-decoration:none;cursor:pointer}
.btn.ghost{background:transparent;color:var(--fg);border:1px solid var(--bd)}
.sql{padding:10px 18px;border-bottom:1px solid var(--bd);overflow:auto}
.sql pre{margin:0;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;color:var(--accent)}
.controls{display:flex;gap:8px;padding:12px 18px}
.controls input{flex:1;padding:7px 10px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--fg)}
.meta{padding:0 18px 10px;font-size:12px;opacity:.7}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--bd);padding:6px 9px;text-align:left;vertical-align:top}
th{background:var(--th);position:sticky;top:0;cursor:pointer;white-space:nowrap}
tr:nth-child(even) td{background:var(--row-alt)}
tr:hover td{background:rgba(37,99,235,0.06)}
footer{padding:14px 18px;font-size:12px;opacity:.6}
"""

    js = """
(function(){
  var saved = localStorage.getItem('unsql-theme');
  if(saved){document.documentElement.setAttribute('data-theme',saved);}
  var tbtn=document.getElementById('theme');
  if(tbtn){tbtn.addEventListener('click',function(){
    var cur=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',cur);
    localStorage.setItem('unsql-theme',cur);
  });}
  var q=document.getElementById('q');
  if(q){q.addEventListener('input',function(){
    var t=q.value.toLowerCase();
    var rows=document.querySelectorAll('tbody tr');
    for(var i=0;i<rows.length;i++){
      rows[i].style.display = rows[i].textContent.toLowerCase().indexOf(t)===-1?'none':'';
    }
  });}
  var ths=document.querySelectorAll('thead th');
  for(var i=0;i<ths.length;i++){(function(th,idx){
    th.addEventListener('click',function(){
      var tb=document.querySelector('tbody');
      var rows=Array.prototype.slice.call(tb.querySelectorAll('tr'));
      var asc = th.dataset.asc !== 'true';
      th.dataset.asc = asc ? 'true' : 'false';
      rows.sort(function(a,b){
        var x=a.children[idx].textContent, y=b.children[idx].textContent;
        var nx=parseFloat(x), ny=parseFloat(y);
        var c;
        if(!isNaN(nx) && !isNaN(ny)){ c = nx-ny; } else { c = x.localeCompare(y); }
        return asc ? c : -c;
      });
      for(var k=0;k<rows.length;k++){tb.appendChild(rows[k]);}
    });
  })(ths[i],i);}
})();
"""

    return (
        "<!doctype html>"
        f'<html data-theme="{_h(theme)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_h(title)}</title><style{na}>{css}</style></head><body>"
        f"<header><h1>{_h(title)}</h1>"
        f'<span class="badge">{len(display_rows)} rows · {len(columns)} cols</span>{trunc_badge}'
        '<span class="spacer"></span><button id="theme" class="btn ghost">Toggle theme</button></header>'
        f"{sql_block}"
        '<div class="controls"><input id="q" type="search" placeholder="Filter rows…">'
        '<a class="btn" href="/api/csv">CSV</a>'
        '<a class="btn ghost" href="/api/json">JSON</a>'
        '<a class="btn ghost" href="/api/data">Raw</a></div>'
        f'<div class="meta">{len(display_rows)} displayed of {len(rows)} total — click a header to sort, type to filter.</div>'
        f"<table><thead><tr>{col_headers}</tr></thead><tbody>{row_html}</tbody></table>"
        "<footer>UNSQL GUI · local only</footer>"
        f"<script{na}>{js}</script></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "UNSQL-GUI"

    def log_message(self, fmt, *args):  # silence
        return

    def _send(self, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self) -> None:
        with _server_lock:
            data = dict(_current_data)
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self._send(body, "application/json", {
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'",
        })

    def _json_file(self) -> None:
        with _server_lock:
            cols = list(_current_data.get("columns") or [])
            rows = list(_current_data.get("rows") or [])
        out = [r if isinstance(r, dict) else dict(zip(cols, list(r))) for r in rows]
        body = json.dumps(out, indent=2, default=str).encode("utf-8")
        self._send(body, "application/json", {
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'",
            "Content-Disposition": 'attachment; filename="unsql_results.json"',
        })

    def _csv(self) -> None:
        with _server_lock:
            cols = list(_current_data.get("columns") or [])
            rows = list(_current_data.get("rows") or [])
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        w.writerow([_sanitize(c) for c in cols])
        for r in rows:
            vals = [r.get(c, "") for c in cols] if isinstance(r, dict) else list(r)
            w.writerow([_sanitize(v) for v in vals])
        body = buf.getvalue().encode("utf-8")
        self._send(body, "text/csv", {
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="unsql_results.csv"',
        })

    def _html(self) -> None:
        nonce = secrets.token_hex(16)
        with _server_lock:
            data = dict(_current_data)
            theme = _current_theme
            max_rows = _current_max
        page = _render_html(
            list(data.get("columns") or []),
            list(data.get("rows") or []),
            sql=data.get("sql") or "",
            title=data.get("title") or "UNSQL Results",
            theme=theme,
            max_rows=max_rows,
            nonce=nonce,
        )
        self._send(page.encode("utf-8"), "text/html; charset=utf-8", {
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
                f"style-src 'self' 'nonce-{nonce}'"
            ),
        })

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/data":
            self._json()
        elif path == "/api/json":
            self._json_file()
        elif path == "/api/csv":
            self._csv()
        elif path in ("/", "/index.html"):
            self._html()
        else:
            self.send_error(404)


def _find_free_port(host: str, port: int) -> int:
    for candidate in range(port, port + 21):
        try:
            with socket.socket() as s:
                s.bind((host, candidate))
            return candidate
        except OSError:
            continue
    return port


def start_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    columns: list[str] | None = None,
    rows: list[Any] | None = None,
    sql: str = "",
    title: str = "UNSQL Results",
    max_rows: int = 10000,
    theme: str = "light",
) -> str:
    global _server, _thread, _current_data, _current_theme, _current_max
    with _server_lock:
        _current_data = {
            "columns": list(columns or []),
            "rows": [list(r) if isinstance(r, (list, tuple)) else r for r in (rows or [])],
            "sql": sql,
            "title": title,
        }
        _current_theme = theme
        _current_max = max_rows

        if _server is not None:
            return f"http://{host}:{_server.server_port}/"

        HTTPServer.allow_reuse_address = False
        bound = False
        attempt_port = port
        last_exc: Exception | None = None
        for _ in range(20):
            try:
                _server = HTTPServer((host, attempt_port), _Handler)
                port = attempt_port
                bound = True
                break
            except OSError as exc:
                last_exc = exc
                attempt_port += 1
                continue
        if not bound:
            raise RuntimeError(f"GUI web server failed to bind {host}:{port}: {last_exc}")
        _thread = threading.Thread(target=_server.serve_forever, name="unsql-gui", daemon=True)
        _thread.start()
        return f"http://{host}:{port}/"


def update_data(columns: list[str], rows: list[Any], sql: str = "", title: str = "UNSQL Results") -> None:
    global _current_data
    with _server_lock:
        _current_data = {
            "columns": list(columns or []),
            "rows": [list(r) if isinstance(r, (list, tuple)) else r for r in (rows or [])],
            "sql": sql,
            "title": title,
        }


def stop_server() -> None:
    global _server, _thread
    with _server_lock:
        if _server is not None:
            try:
                _server.shutdown()
                _server.server_close()
            except Exception:
                pass
            if _thread is not None:
                _thread.join(timeout=2)
        _server = None
        _thread = None
