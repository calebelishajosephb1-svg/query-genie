"""
unsql/engines/mysql.py
-----------------------
MySQL engine adapter using mysql-connector-python.

Auto-discovers MySQL on port 3306 (localhost).
"""
from __future__ import annotations

import os
import re
import socket
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import mysql.connector
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class MySQLEngine(DBEngine):
    """Adapter for MySQL."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None

    @property
    def engine_name(self) -> str:
        return "MySQL"

    @property
    def engine_type(self) -> str:
        return "mysql"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError("MySQL driver not installed. Run: pip install mysql-connector-python")
        host = kwargs.get("host") or "localhost"
        port_raw = kwargs.get("port")
        port = int(port_raw) if port_raw else 3306
        database = kwargs.get("database") or None
        # TLS: honor ssl_mode / ssl_ca if provided, else warn about cleartext (checklist §3)
        ssl_disabled = kwargs.get("ssl_disabled", False)
        if not kwargs.get("ssl_ca") and not kwargs.get("ssl_mode") and host not in ("localhost", "127.0.0.1", "::1"):
            try:
                if not ssl_disabled:
                    print(f"  [Security] MySQL connection to {host} without TLS — traffic may be unencrypted. Use ssl_mode=REQUIRED for TLS.")
            except Exception:
                pass
        try:
            config = dict(host=host, port=port, user=username, password=password,
                          connection_timeout=10, autocommit=False)
            if database:
                config["database"] = database
            # Pass TLS opts through if provided
            for k in ("ssl_ca", "ssl_cert", "ssl_key", "ssl_mode", "ssl_disabled"):
                if kwargs.get(k) is not None:
                    config[k] = kwargs[k]
            self._conn = mysql.connector.connect(**config)
            self._cursor = self._conn.cursor()
        except Exception as exc:
            from unsql.core.security.redact import redact as _redact
            safe_msg = _redact(str(exc))
            raise ConnectionError(f"MySQL connection failed: {safe_msg}") from None

    def disconnect(self) -> None:
        for obj in (self._cursor, self._conn):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self._conn = None
        self._cursor = None

    # ── Capabilities ───────────────────────────────────────────────────────────

    @property
    def ddl_auto_commits(self) -> bool:
        # DDL in this engine implicitly commits the transaction.
        return True

    def is_connected(self) -> bool:
        try:
            return self._conn is not None and self._conn.is_connected()
        except Exception:
            return False

    def execute(self, sql: str) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(`?\w+`?)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1).strip("`"))

        try:
            self._cursor.execute(sql)
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                rows = self._cursor.fetchall()
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=self._cursor.rowcount or 0, is_select=False)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def _describe(self, table: str) -> QueryResult:
        try:
            table_esc = table.replace("`", "``")
            self._cursor.execute(f"DESCRIBE `{table_esc}`")
            cols = [d[0] for d in self._cursor.description]
            rows = self._cursor.fetchall()
            return QueryResult(columns=cols, rows=[tuple(r) for r in rows],
                               rowcount=len(rows), is_select=True, is_describe=True)
        except Exception as exc:
            raise RuntimeError(f"DESCRIBE failed for {table}: {exc}") from exc

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []

        tables: dict[str, TableSchema] = {}
        # Current database
        self._cursor.execute("SELECT DATABASE()")
        (db_name,) = self._cursor.fetchone()
        if not db_name:
            return []

        self._cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name", (db_name,)
        )
        for (tname,) in self._cursor.fetchall():
            tables[tname] = TableSchema(name=tname, columns=[])

        self._cursor.execute(
            "SELECT table_name, column_name, column_type, is_nullable, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (db_name,)
        )
        for tname, cname, ctype, nullable, col_key in self._cursor.fetchall():
            if tname not in tables:
                continue
            tables[tname].columns.append(ColumnInfo(
                name=cname, type=ctype,
                nullable=(nullable == "YES"),
                primary_key=(col_key == "PRI"),
            ))

        # FKs
        self._cursor.execute("""
            SELECT table_name, column_name,
                   referenced_table_name, referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_schema = %s
              AND referenced_table_name IS NOT NULL
        """, (db_name,))
        for tname, cname, ref_table, ref_col in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.foreign_key = f"{ref_table}.{ref_col}"

        return list(tables.values())


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace("`", "``")
        return "`" + escaped + "`"

    def parameter_style(self) -> str:
        return "pyformat"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        # MySQL 8 supports CTE/window, but not returning
        return feat in {"cte", "window_functions", "check_constraints", "savepoint", "identity"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if offset is not None:
            parts.append(f"OFFSET {offset}")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": None}

    def transaction_behavior(self) -> dict:
        # MySQL DDL auto commits
        return {"supports_savepoint": True, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "REPEATABLE_READ"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        try:
            with socket.create_connection(("localhost", 3306), timeout=1):
                hints.append("localhost:3306 (MySQL port open)")
        except (socket.timeout, ConnectionRefusedError, OSError):
            hints.append("localhost:3306 (default — not detected)")
        return hints
