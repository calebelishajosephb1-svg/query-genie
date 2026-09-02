"""
unsql/engines/sqlite.py
------------------------
SQLite engine adapter using the built-in sqlite3 module.

No external driver needed — sqlite3 is part of the Python standard library.

Connection:
  - File path provided by the user (or prompted by commands.py)
  - Also accepts ":memory:" for an in-memory database
  - Enables foreign key enforcement automatically (PRAGMA foreign_keys = ON)
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema


class SQLiteEngine(DBEngine):
    """Adapter for SQLite using the built-in sqlite3 module."""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._file_path: str = ""

    @property
    def engine_name(self) -> str:
        return "SQLite"

    @property
    def engine_type(self) -> str:
        return "sqlite"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        """
        username/password are ignored for SQLite — it's file-based.
        Expects kwargs['file_path'] from commands.py prompt.
        """
        file_path = kwargs.get("file_path") or ":memory:"
        self._file_path = file_path

        try:
            self._conn = sqlite3.connect(file_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable FK enforcement
            cur = self._conn.cursor()
            try:
                cur.execute("PRAGMA foreign_keys = ON")
            finally:
                cur.close()
            self._conn.commit()
        except Exception as exc:
            raise ConnectionError(f"SQLite connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def is_connected(self) -> bool:
        return self._conn is not None

    # ── Capabilities ───────────────────────────────────────────────────────────

    @property
    def paramstyle(self) -> str | None:
        return "qmark"

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: list | tuple | None = None) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        # Intercept DESCRIBE / DESC
        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1))

        # Intercept PRAGMA table_info (AI may generate this for DESCRIBE)
        pragma_match = re.match(r"^PRAGMA\s+table_info\s*\(\s*['\"]?(\w+)['\"]?\s*\)$",
                                sql, re.IGNORECASE)
        if pragma_match:
            return self._describe(pragma_match.group(1))

        cur = self._conn.cursor()
        try:
            if params:
                cur.execute(sql, tuple(params))
            else:
                cur.execute(sql)
            sql_up = sql.upper().lstrip()
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = [tuple(row) for row in cur.fetchall()]
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=cur.rowcount or 0, is_select=False)
        except sqlite3.Error as exc:
            from ..redact import redact as _redact
            safe = _redact(str(exc))
            # Classify error for actionable message without leaking internal paths/home dir
            raise RuntimeError(safe) from None
        finally:
            cur.close()

    def executemany(self, sql: str, params_seq: list[list | tuple]) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        cur = self._conn.cursor()
        try:
            cur.executemany(sql, params_seq)
            return QueryResult(columns=[], rows=[], rowcount=cur.rowcount or 0, is_select=False)
        except sqlite3.Error as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            cur.close()

    def _describe(self, table: str) -> QueryResult:
        """Use PRAGMA table_info to emulate DESCRIBE."""
        # Validate identifier to prevent injection via PRAGMA string interpolation
        try:
            from ..core.security.validate import validate_identifier
            validate_identifier(table, None)
        except Exception:
            raise RuntimeError(f"Invalid table name for DESCRIBE: {table}")
        cur = self._conn.cursor()
        try:
            table_esc = table.replace("'", "''")
            cur.execute(f"PRAGMA table_info('{table_esc}')")
            # columns: cid, name, type, notnull, dflt_value, pk
            rows_raw = cur.fetchall()
            cols = ["Name", "Null?", "Type"]
            rows = []
            for r in rows_raw:
                r = tuple(r)
                name = r[1]
                dtype = r[2] or "TEXT"
                notnull = r[3]  # 1 = NOT NULL
                null_str = "NOT NULL" if notnull else ""
                rows.append((name, null_str, dtype.upper()))
            return QueryResult(columns=cols, rows=rows, rowcount=len(rows),
                               is_select=True, is_describe=True)
        except Exception as exc:
            raise RuntimeError(f"DESCRIBE failed for {table}: {exc}") from exc
        finally:
            cur.close()

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass

    # ── Schema ────────────────────────────────────────────────────────────────

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []

        tables: dict[str, TableSchema] = {}

        cur = self._conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            for (tname,) in cur.fetchall():
                if tname.startswith("sqlite_"):
                    continue
                # Hide UNSQL internal bookkeeping tables from user-visible schema
                if tname.upper().startswith("UNSQL_"):
                    continue
                tables[tname] = TableSchema(name=tname, columns=[])

            for tname, ts in list(tables.items()):
                tname_esc = tname.replace("'", "''")
                cur.execute(f"PRAGMA table_info('{tname_esc}')")
                for row in cur.fetchall():
                    row = tuple(row)
                    # cid, name, type, notnull, dflt_value, pk
                    cid, cname, ctype, notnull, dflt, is_pk = row
                    col = ColumnInfo(
                        name=cname,
                        type=(ctype or "TEXT").upper(),
                        nullable=(not notnull),
                        primary_key=bool(is_pk),
                    )
                    ts.columns.append(col)

                # Foreign keys via PRAGMA
                cur.execute(f"PRAGMA foreign_key_list('{tname_esc}')")
                for fk in cur.fetchall():
                    fk = tuple(fk)
                    # id, seq, table, from, to, ...
                    from_col, to_table, to_col = fk[3], fk[2], fk[4]
                    for col in ts.columns:
                        if col.name == from_col:
                            col.foreign_key = f"{to_table}.{to_col}"
        finally:
            cur.close()

        return list(tables.values())

    # ── Discovery ─────────────────────────────────────────────────────────────


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        return "qmark"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        return feat in {"cte", "window_functions", "check_constraints", "savepoint"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if offset is not None:
            parts.append(f"OFFSET {offset}")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": False, "insert_omit": False, "returning_clause": None}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": True, "isolation_levels": ["READ_COMMITTED", "SERIALIZABLE"]}


    def discover(self) -> list[str]:
        """Scan the current directory and home directory for .db/.sqlite files."""
        hints: list[str] = []
        search_dirs = [Path.cwd(), Path.home()]
        extensions = {".db", ".sqlite", ".sqlite3"}
        seen = set()

        for d in search_dirs:
            try:
                for f in sorted(d.iterdir()):
                    if f.is_file() and f.suffix.lower() in extensions:
                        fp = str(f.resolve())
                        if fp not in seen:
                            hints.append(fp)
                            seen.add(fp)
            except PermissionError:
                pass

        return hints
