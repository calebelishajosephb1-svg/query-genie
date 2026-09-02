"""
unsql/render.py
---------------
Terminal output: result tables, SQL echo, status lines.
Uses rich when installed, falls back to plain ASCII otherwise.
"""
from __future__ import annotations

from typing import Any, Sequence

try:
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.table import Table

    _console: Console | None = Console()
except Exception:  # pragma: no cover - rich is a hard dep but stay graceful
    _console = None


def _cell(value: Any) -> str:
    return "NULL" if value is None else str(value)


def info(msg: str) -> None:
    print(msg) if _console is None else _console.print(msg, style="cyan")


def warn(msg: str) -> None:
    print(msg) if _console is None else _console.print(msg, style="yellow")


def error(msg: str) -> None:
    print(msg) if _console is None else _console.print(msg, style="bold red")


def plain(msg: str = "") -> None:
    print(msg) if _console is None else _console.print(msg)


def sql(text: str) -> None:
    if _console is None:
        print(text)
    else:
        _console.print(Syntax(text, "sql", theme="ansi_dark", word_wrap=True))


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]], max_rows: int = 200) -> None:
    shown = list(rows[:max_rows])
    if _console is None:
        widths = [
            max(len(str(c)), *(len(_cell(r[i])) for r in shown)) if shown else len(str(c))
            for i, c in enumerate(columns)
        ]
        line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        print(line)
        print("| " + " | ".join(str(c).ljust(w) for c, w in zip(columns, widths)) + " |")
        print(line)
        for r in shown:
            print("| " + " | ".join(_cell(v).ljust(w) for v, w in zip(r, widths)) + " |")
        print(line)
    else:
        t = Table(show_lines=False, header_style="bold magenta", border_style="grey37")
        for c in columns:
            t.add_column(str(c), overflow="fold")
        for r in shown:
            t.add_row(*[_cell(v) for v in r])
        _console.print(t)
    if len(rows) > len(shown):
        warn(f"  ... {len(rows) - len(shown)} more rows (use `save <file>;` for the full output)")
