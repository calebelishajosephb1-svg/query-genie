"""
unsql/engines/postgres.py
--------------------------
PostgreSQL engine adapter using psycopg2.

Auto-discovers local PostgreSQL instances by checking port 5432
and the PGHOST / PGPORT / PGDATABASE environment variables.
"""
from __future__ import annotations

import os
import socket
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import psycopg2
    import psycopg2.extras
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _parse_pg_cost(explain_line: str) -> float | None:
    """
    Parse PostgreSQL EXPLAIN output's cost from a line like:
        Seq Scan on t  (cost=0.00..35.50 rows=10 width=4)
    Returns the upper-bound cost (35.50 above), or None.
    """
    import re as _re
    m = _re.search(r"cost=\d+(?:\.\d+)?\.\.(\d+(?:\.\d+)?)", explain_line)
    return float(m.group(1)) if m else None


class PostgresEngine(DBEngine):
    """Adapter for PostgreSQL."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None

    @property
    def engine_name(self) -> str:
        return "PostgreSQL"

    @property
    def engine_type(self) -> str:
        return "postgresql"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "PostgreSQL driver not installed. Run: pip install psycopg2-binary"
            )
        host = kwargs.get("host") or os.environ.get("PGHOST", "localhost")
        port_raw = kwargs.get("port") or os.environ.get("PGPORT", 5432)
        port = int(port_raw) if port_raw else 5432
        database = kwargs.get("database") or os.environ.get("PGDATABASE", username)

        # TLS: use sslmode if provided, otherwise Warn about unencrypted transport (checklist §3)
        sslmode = kwargs.get("sslmode") or kwargs.get("ssl_mode")
        extra_kwargs: dict[str, Any] = {}
        if sslmode:
            extra_kwargs["sslmode"] = sslmode
        else:
            # Default to prefer (opportunistic TLS) where supported; warn user if cleartext
            try:
                import warnings
                # Only warn for non-localhost to reduce noise for local dev
                if host not in ("localhost", "127.0.0.1", "::1"):
                    print(f"  [Security] PostgreSQL connection to {host} without sslmode — traffic may be unencrypted. Use sslmode=require for TLS.")
            except Exception:
                pass
        try:
            self._conn = psycopg2.connect(
                host=host, port=port, dbname=database,
                user=username, password=password,
                connect_timeout=10,
                **extra_kwargs,
            )
            self._conn.autocommit = False
            self._cursor = self._conn.cursor()
        except Exception as exc:
            # Sanitize error — don't leak connection string / password (checklist §13)
            from unsql.core.security.redact import redact as _redact
            safe_msg = _redact(str(exc))
            raise ConnectionError(f"PostgreSQL connection failed: {safe_msg}") from None

    def disconnect(self) -> None:
        for obj in (self._cursor, self._conn):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self._conn = None
        self._cursor = None

    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, sql: str) -> QueryResult | None:
        import re
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        # Intercept DESCRIBE / \d equivalent
        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1).lower())

        try:
            self._cursor.execute(sql)
            sql_up = sql.upper().lstrip()
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                rows = self._cursor.fetchall()
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=self._cursor.rowcount or 0, is_select=False)
        except Exception as exc:
            self._conn.rollback()
            raise RuntimeError(str(exc)) from exc

    def _describe(self, table: str) -> QueryResult:
        sql = """
            SELECT column_name, is_nullable,
                   data_type ||
                   CASE WHEN character_maximum_length IS NOT NULL
                        THEN '(' || character_maximum_length || ')'
                        WHEN numeric_precision IS NOT NULL AND numeric_scale IS NOT NULL
                        THEN '(' || numeric_precision || ',' || numeric_scale || ')'
                        ELSE '' END AS full_type
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position
        """
        try:
            self._cursor.execute(sql, (table,))
            cols = ["Name", "Null?", "Type"]
            rows = []
            for cname, nullable, ftype in self._cursor.fetchall():
                null_str = "" if nullable == "YES" else "NOT NULL"
                rows.append((cname, null_str, ftype))
            return QueryResult(columns=cols, rows=rows, rowcount=len(rows),
                               is_select=True, is_describe=True)
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

    def get_query_cost(self, sql: str) -> float | None:
        """
        EXPLAIN the query and return PostgreSQL's estimated total cost
        (the upper bound of `cost=startup..total`). Returns None when the
        plan can't be produced or parsed — callers treat None as "skip".
        """
        if not self._conn or self._conn.closed:
            return None
        try:
            cur = self._conn.cursor()
            try:
                cur.execute("EXPLAIN " + sql)
                row = cur.fetchone()
            finally:
                cur.close()
            if row and row[0]:
                return _parse_pg_cost(str(row[0]))
        except Exception:
            return None
        return None

    # ── Schema ────────────────────────────────────────────────────────────────

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []

        tables: dict[str, TableSchema] = {}

        self._cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        for (tname,) in self._cursor.fetchall():
            if tname.upper().startswith("UNSQL_"):
                continue
            tables[tname] = TableSchema(name=tname, columns=[])

        self._cursor.execute("""
            SELECT table_name, column_name, data_type,
                   character_maximum_length, numeric_precision, numeric_scale,
                   is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        for row in self._cursor.fetchall():
            tname, cname, dtype, clen, nprec, nscale, nullable = row
            if tname not in tables:
                continue
            ts = dtype
            if clen:
                ts = f"{dtype}({clen})"
            elif nprec and nscale:
                ts = f"{dtype}({nprec},{nscale})"
            col = ColumnInfo(name=cname, type=ts, nullable=(nullable == "YES"))
            tables[tname].columns.append(col)

        # PKs
        self._cursor.execute("""
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = 'public'
            ORDER BY kcu.table_name, kcu.ordinal_position
        """)
        for tname, cname in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.primary_key = True
                        col.nullable = False

        # FKs
        self._cursor.execute("""
            SELECT
                kcu.table_name, kcu.column_name,
                ccu.table_name AS ref_table, ccu.column_name AS ref_col
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu
              ON rc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON rc.unique_constraint_name = ccu.constraint_name
            WHERE kcu.table_schema = 'public'
        """)
        for tname, cname, ref_table, ref_col in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.foreign_key = f"{ref_table}.{ref_col}"

        return list(tables.values())

    # ── Discovery ─────────────────────────────────────────────────────────────


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        return "pyformat"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        return feat in {"cte", "window_functions", "returning", "identity", "generated_columns", "check_constraints", "savepoint"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if offset is not None:
            parts.append(f"OFFSET {offset}")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": "RETURNING"}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": True, "isolation_levels": ["READ_COMMITTED", "REPEATABLE_READ", "SERIALIZABLE"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        host = os.environ.get("PGHOST", "localhost")
        port = int(os.environ.get("PGPORT", 5432))
        try:
            with socket.create_connection((host, port), timeout=1):
                hints.append(f"{host}:{port} (PostgreSQL port open)")
        except (socket.timeout, ConnectionRefusedError, OSError):
            hints.append(f"{host}:{port} (default — not detected)")
        return hints
