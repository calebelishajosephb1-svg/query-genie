"""
unsql/agent.py
--------------
The always-on multi-agent loop.

Simple requests take the fast path (one SQL pass). Complex / chained requests
auto-escalate: plan -> write SQL -> static review against the live schema ->
execute step by step inside savepoints, self-correcting failing statements.

Nothing here stores data: everything is grounded in, and executed against, the
live connected engine.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .prompts import plan_system, repair_system, review_system, sql_system, strip_fences

Status = Callable[[str], None]

_COMPLEX_HINTS = (
    "and then", "after that", "compare", "rank", "top ", "each", "every", "then ",
    "update", "delete", "drop", "insert", "seed", "create", "schema", "report",
    "percentage", "average", "highest", "lowest", "trend", "recalculate", "all tables",
)


def split_statements(script: str) -> list[str]:
    """Split on ';' outside quotes and comments."""
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(script):
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and script[i : i + 2] == "--":
            while i < len(script) and script[i] != "\n":
                i += 1
            buf.append("\n")
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail and tail != "/":
        out.append(tail)
    return [s for s in out if _meaningful(s)]


def _meaningful(stmt: str) -> bool:
    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
    return bool(body.strip())


def statement_kind(stmt: str) -> str:
    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
    return (body.split(None, 1)[0].lower() if body else "")


_DROP_RE = re.compile(r"drop\s+table\s+(?:if\s+exists\s+)?[`\"\[]?([\w.]+)", re.I)


def drop_target(stmt: str) -> str | None:
    m = _DROP_RE.search(stmt)
    return m.group(1).split(".")[-1].lower() if m else None


def reorder_drops(statements: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    """Move DROP TABLE statements children-first (a child references a parent)."""
    idx = [i for i, s in enumerate(statements) if drop_target(s)]
    if len(idx) < 2:
        return statements
    drops = [statements[i] for i in idx]
    names = {drop_target(s) or "": s for s in drops}

    ordered: list[str] = []
    remaining = dict(names)
    while remaining:
        # a table is safe to drop when nothing still-remaining references it
        safe = [
            t for t in remaining
            if not any(t in dependencies.get(other, set()) for other in remaining if other != t)
        ]
        if not safe:
            safe = list(remaining)
        for t in sorted(safe):
            ordered.append(remaining.pop(t))
    out = list(statements)
    for slot, stmt in zip(idx, ordered):
        out[slot] = stmt
    return out


class Agent:
    """Plans, writes, reviews and repairs SQL for one live connection."""

    def __init__(self, ai: Any, engine: Any, status: Status) -> None:
        self.ai = ai
        self.engine = engine
        self.status = status
        self.last_plan = ""

    # ── helpers ──────────────────────────────────────────────────────────────

    def _schema(self, request: str = "") -> str:
        try:
            return self.engine.schema_to_text(request) or ""
        except Exception:
            return ""

    def dependencies(self) -> dict[str, set[str]]:
        """table -> set of tables it references (via foreign keys)."""
        deps: dict[str, set[str]] = {}
        try:
            for tbl in self.engine.get_schema() or []:
                refs = set()
                for col in tbl.columns:
                    if col.foreign_key:
                        refs.add(col.foreign_key.split(".")[0].lower())
                deps[tbl.name.lower()] = refs
        except Exception:
            pass
        return deps

    @staticmethod
    def is_complex(request: str) -> bool:
        low = request.lower()
        hits = sum(1 for h in _COMPLEX_HINTS if h in low)
        return hits >= 2 or len(low) > 220

    # ── the loop ─────────────────────────────────────────────────────────────

    def plan_and_write(self, request: str) -> list[str]:
        schema = self._schema(request)
        plan = ""
        if self.is_complex(request):
            self.status("  agent · planning against the live schema")
            plan = self.ai.complete(plan_system(self.engine.engine_type, schema), request).strip()
            self.last_plan = plan
            self.status("  agent · writing SQL")
        else:
            self.status("  agent · translating")
        sql = strip_fences(
            self.ai.complete(sql_system(self.engine.engine_type, schema, plan), request)
        )
        if plan:
            self.status("  agent · verifying syntax, constraints and dependency order")
            reviewed = strip_fences(
                self.ai.complete(
                    review_system(self.engine.engine_type, schema),
                    f"REQUEST:\n{request}\n\nPLAN:\n{plan}\n\nSCRIPT:\n{sql}",
                )
            )
            if reviewed and reviewed.strip().upper() not in ("OK", "OK."):
                sql = reviewed
        statements = split_statements(sql)
        return reorder_drops(statements, self.dependencies())

    def repair(self, statement: str, error: str) -> list[str]:
        """Ask for a corrected version of one failing step. [] means skip it."""
        fixed = strip_fences(
            self.ai.complete(
                repair_system(self.engine.engine_type, self._schema()),
                f"FAILING STATEMENT:\n{statement}\n\nENGINE ERROR:\n{error}",
            )
        )
        if not fixed or fixed.strip().upper().startswith("SKIP"):
            return []
        return split_statements(fixed)
