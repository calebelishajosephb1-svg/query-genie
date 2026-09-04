"""
unsql/gui/visualizer.py
-----------------------
Bridge between REPL execution and the two GUI surfaces
(Rich terminal paginator + local web dashboard) plus CSV/JSON exports.
"""
from __future__ import annotations

import csv
import json
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from ..core.termsetup import setup_console

    setup_console()
except Exception:  # pragma: no cover
    pass

try:
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.table import Table

    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False
    Console = Prompt = Table = None  # type: ignore

_last_result: dict[str, Any] = {}
_lock = threading.Lock()
_default_viz: "GUIVisualizer | None" = None


@dataclass
class GUIConfig:
    web_port: int = 8765
    web_host: str = "127.0.0.1"
    page_size: int = 100
    max_rows_web: int = 10000
    auto_open_browser: bool = True
    theme: str = "light"


class GUIVisualizer:
    def __init__(self, config: GUIConfig | None = None, console: Any | None = None) -> None:
        self.config = config or GUIConfig()
        self.console = console if console is not None else (Console() if _RICH else None)
        self._history: list[dict[str, Any]] = []

    # ── data plumbing ────────────────────────────────────────────────────
    def push_result(self, columns: list[str], rows: list[Any], sql: str = "", title: str = "Result") -> None:
        global _last_result
        entry = {
            "columns": list(columns or []),
            "rows": [list(r) if isinstance(r, (list, tuple)) else r for r in (rows or [])],
            "sql": sql,
            "title": title,
        }
        with _lock:
            _last_result = entry
            self._history.append(entry)
        try:
            from .web import update_data, _server

            if _server is not None:
                update_data(entry["columns"], entry["rows"], entry["sql"], entry["title"])
        except Exception:
            pass

    def get_last(self) -> dict[str, Any] | None:
        with _lock:
            return dict(_last_result) if _last_result else None

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def _resolve(self, columns, rows):
        if columns is None or rows is None:
            src = self.get_last() or {}
            columns = columns if columns is not None else list(src.get("columns") or [])
            rows = rows if rows is not None else list(src.get("rows") or [])
        return list(columns or []), list(rows or [])

    # ── terminal viewer ──────────────────────────────────────────────────
    def launch_terminal(self, columns: list[str] | None = None, rows: list[Any] | None = None,
                        title: str = "Result") -> None:
        columns, rows = self._resolve(columns, rows)
        if not rows:
            msg = "No result to visualize"
            if _RICH and self.console:
                self.console.print(f"[yellow]{msg}[/yellow]")
            else:
                print(msg)
            return

        if not _RICH or self.console is None:
            print(" | ".join(str(c) for c in columns))
            for r in rows[:100]:
                print(" | ".join("" if v is None else str(v) for v in r))
            return

        page_size = max(1, self.config.page_size)
        total = len(rows)
        total_pages = (total + page_size - 1) // page_size

        def render(page_no: int) -> Any:
            start = (page_no - 1) * page_size
            chunk = rows[start:start + page_size]
            t = Table(title=f"{title} — page {page_no}/{total_pages} ({total} rows)",
                      header_style="bold magenta", border_style="grey37")
            for c in columns:
                t.add_column(str(c), overflow="fold")
            for r in chunk:
                t.add_row(*["NULL" if v is None else str(v) for v in r])
            return t

        if total <= page_size:
            self.console.print(render(1))
            return

        page = 1
        while True:
            self.console.clear()
            self.console.print(render(page))
            self.console.print("[dim]n next · p prev · g go to · / search · q quit[/dim]")
            try:
                cmd = Prompt.ask("gui", default="q").strip()
            except (KeyboardInterrupt, EOFError):
                break
            low = cmd.lower()
            if low in ("q", "quit", "exit", ""):
                break
            if low in ("n", "next"):
                page = min(total_pages, page + 1)
            elif low in ("p", "prev"):
                page = max(1, page - 1)
            elif low.startswith("g"):
                target = low[1:].strip()
                if not target:
                    try:
                        target = Prompt.ask("page", default=str(page)).strip()
                    except (KeyboardInterrupt, EOFError):
                        break
                try:
                    page = max(1, min(total_pages, int(target)))
                except ValueError:
                    pass
            elif cmd.startswith("/"):
                term = cmd[1:].strip().lower()
                hits = [r for r in rows if any(term in str(v).lower() for v in r)]
                t = Table(title=f"search '{term}' — {len(hits)} match(es)",
                          header_style="bold magenta", border_style="grey37")
                for c in columns:
                    t.add_column(str(c), overflow="fold")
                for r in hits[:page_size]:
                    t.add_row(*["NULL" if v is None else str(v) for v in r])
                self.console.print(t)
                try:
                    Prompt.ask("press enter to return", default="")
                except (KeyboardInterrupt, EOFError):
                    break

    # ── web dashboard ────────────────────────────────────────────────────
    def launch_web(self, columns: list[str] | None = None, rows: list[Any] | None = None,
                   sql: str = "", title: str = "UNSQL Results",
                   open_browser: bool | None = None) -> str:
        from .web import start_server, update_data, _server as _running

        columns, rows = self._resolve(columns, rows)
        url = start_server(
            host=self.config.web_host,
            port=self.config.web_port,
            columns=columns,
            rows=rows,
            sql=sql,
            title=title,
            max_rows=self.config.max_rows_web,
            theme=self.config.theme,
        )
        update_data(columns, rows, sql, title)
        should_open = self.config.auto_open_browser if open_browser is None else open_browser
        if should_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        msg = f"GUI web dashboard: {url}"
        if _RICH and self.console:
            self.console.print(f"[green]GUI web dashboard:[/green] {url}")
        else:
            print(msg)
        return url

    # ── exports ──────────────────────────────────────────────────────────
    def export_csv(self, path: str | Path, columns: list[str] | None = None,
                   rows: list[Any] | None = None) -> Path:
        columns, rows = self._resolve(columns, rows)
        from .web import _sanitize

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
            w.writerow([_sanitize(c) for c in columns])
            for r in rows:
                vals = [r.get(c, "") for c in columns] if isinstance(r, dict) else list(r)
                w.writerow([_sanitize(v) for v in vals])
        return p

    def export_json(self, path: str | Path, columns: list[str] | None = None,
                    rows: list[Any] | None = None) -> Path:
        columns, rows = self._resolve(columns, rows)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        out = [r if isinstance(r, dict) else dict(zip(columns, list(r))) for r in rows]
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        return p


def get_visualizer() -> GUIVisualizer:
    global _default_viz
    if _default_viz is None:
        _default_viz = GUIVisualizer()
    return _default_viz


def launch_terminal(*args, **kwargs):
    return get_visualizer().launch_terminal(*args, **kwargs)


def launch_web(*args, **kwargs) -> str:
    return get_visualizer().launch_web(*args, **kwargs)
