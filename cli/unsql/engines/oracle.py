"""
unsql/engines/oracle.py
-----------------------
Oracle Database engine adapter using the oracledb driver.

Auto-discovers local Oracle instances via:
  - ORACLE_HOME environment variable
  - TNS_ADMIN / tnsnames.ora presence
  - Running oracle.exe / tnslsnr process detection

Connection: host:port/service_name (defaults localhost:1521/XE)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import oracledb
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _sanitize_oracle_sql(sql: str) -> str:
    """
    Auto-quote bare Oracle reserved keywords in DDL, translate non-Oracle data types,
    and format date/timestamp literals via the Oracle AI adapter plug.
    """
    from ..ai.adapters import get_ai_adapter
    adapter = get_ai_adapter("oracle")
    s = adapter.sanitize_ddl(sql)
    s = adapter.sanitize_dql_dml(s)
    return s


def _build_rac_dsn(
    hosts: list[str],
    port: int,
    service: str,
    *,
    load_balance: bool = True,
    failover: bool = True,
) -> str:
    """
    Build an Oracle RAC TNS descriptor for SCAN / multi-host.
    Example: (DESCRIPTION=(LOAD_BALANCE=on)(FAILOVER=on)(ADDRESS=...)(ADDRESS=...)(CONNECT_DATA=(SERVICE_NAME=svc)))
    """
    addr_parts = "".join(
        f"(ADDRESS=(PROTOCOL=TCP)(HOST={h.strip()})(PORT={port}))" for h in hosts if h.strip()
    )
    lb = "on" if load_balance else "off"
    fo = "on" if failover else "off"
    return (
        f"(DESCRIPTION=(LOAD_BALANCE={lb})(FAILOVER={fo})"
        f"{addr_parts}(CONNECT_DATA=(SERVICE_NAME={service})))"
    )


def _parse_hosts(host_value: str | None) -> list[str]:
    """Parse comma-separated hosts or SCAN-style host string."""
    if not host_value:
        return ["localhost"]
    # Support hosts passed as "host1,host2" or "scan-host"
    return [h.strip() for h in host_value.split(",") if h.strip()]


def _is_rac_service(service: str | None, hosts: list[str] | None = None, rac_hint: bool = False) -> bool:
    """Heuristic: RAC if multiple hosts, service contains RAC/SCAN, or explicit hint."""
    if rac_hint:
        return True
    if hosts and len(hosts) > 1:
        return True
    if service and any(k in service.upper() for k in ("RAC", "SCAN", "CLUSTER")):
        return True
    return False


_sanitize_oracle_ddl = _sanitize_oracle_sql


class OracleEngine(DBEngine):
    """Adapter for Oracle Database (12c+, using python-oracledb thin mode)."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None
        self._is_rac: bool = False
        self._rac_hosts: list[str] = []

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def engine_name(self) -> str:
        return "Oracle Database"

    @property
    def engine_type(self) -> str:
        return "oracle"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "Oracle driver not installed. Run: pip install oracledb"
            )
        # Support RAC: host may be comma-separated, plus explicit hosts/scan kwargs
        host_raw = kwargs.get("host") or kwargs.get("hosts") or kwargs.get("scan") or "localhost"
        hosts = _parse_hosts(str(host_raw))
        # Back-compat: also accept explicit 'hosts' list
        if isinstance(kwargs.get("hosts"), (list, tuple)):
            hosts = [str(h).strip() for h in kwargs["hosts"] if str(h).strip()]
        port_raw = kwargs.get("port")
        port = int(port_raw) if port_raw else 1521
        service = kwargs.get("service") or kwargs.get("service_name") or None
        sid = kwargs.get("sid") or None
        rac_hint = bool(kwargs.get("rac") or kwargs.get("is_rac"))
        is_rac = _is_rac_service(service, hosts, rac_hint)

        # Build DSN — RAC uses TNS descriptor with failover/load_balance
        if is_rac and service and len(hosts) > 1:
            # Prefer TNS descriptor for true RAC; fall back to per-host try if descriptor fails
            tns_dsn = _build_rac_dsn(hosts, port, service)
            try:
                self._conn = oracledb.connect(user=username, password=password, dsn=tns_dsn)
                self._cursor = self._conn.cursor()
                self._is_rac = True
                self._rac_hosts = hosts
                return
            except Exception:
                # Fall through to per-host sequential attempt
                pass
        if service:
            # For single-host or failed RAC TNS, try each host sequentially
            last_exc: Exception | None = None
            for h in hosts:
                try:
                    dsn = f"{h}:{port}/{service}"
                    self._conn = oracledb.connect(user=username, password=password, dsn=dsn)
                    self._cursor = self._conn.cursor()
                    self._is_rac = is_rac
                    self._rac_hosts = hosts
                    return
                except Exception as exc:
                    last_exc = exc
                    continue
            from ..redact import redact as _redact2
            safe2 = _redact2(str(last_exc)) if last_exc else ""
            raise ConnectionError(f"Oracle connection failed (RAC={is_rac}, hosts={hosts}): {safe2}") from None
        elif sid:
            # SID is single-instance only; use first host
            dsn = oracledb.makedsn(hosts[0], port, sid=sid)
        else:
            # No service/sid: try common default services across hosts
            last_exc = None
            for svc in ("FREEPDB1", "XE", "ORCL", "XEPDB1"):
                for h in hosts:
                    try:
                        dsn = f"{h}:{port}/{svc}"
                        conn = oracledb.connect(user=username, password=password, dsn=dsn)
                        self._conn = conn
                        self._cursor = conn.cursor()
                        self._is_rac = is_rac
                        self._rac_hosts = hosts
                        return
                    except Exception as exc:
                        last_exc = exc
                        continue
            raise ConnectionError(
                f"Could not connect to Oracle at {hosts}:{port}. "
                "Try: connect oracle  then specify host/service."
            )

        try:
            self._conn = oracledb.connect(user=username, password=password, dsn=dsn)
            self._cursor = self._conn.cursor()
            self._is_rac = is_rac
            self._rac_hosts = hosts
        except Exception as exc:
            from ..redact import redact as _redact
            safe = _redact(str(exc))
            raise ConnectionError(f"Oracle connection failed: {safe}") from None

    def is_rac(self) -> bool:
        """Return True if this connection was established as RAC."""
        return bool(getattr(self, "_is_rac", False))

    def rac_hosts(self) -> list[str]:
        """Return list of RAC hosts if RAC, else single host list."""
        return list(getattr(self, "_rac_hosts", [])) or ["localhost"]

    def disconnect(self) -> None:
        for obj in (self._cursor, self._conn):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self._conn = None
        self._cursor = None

    def is_connected(self) -> bool:
        return self._conn is not None

    # ── Capabilities ───────────────────────────────────────────────────────────

    @property
    def paramstyle(self) -> str | None:
        return "numeric"

    @property
    def ddl_auto_commits(self) -> bool:
        # Oracle DDL issues an implicit COMMIT before and after execution.
        return True

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: list | tuple | None = None) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        # Intercept DESCRIBE / DESC
        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1).upper())

        # Bound statements were already sanitized upstream (adapter pass)
        # and carry :n placeholders that text rewrites could corrupt —
        # execute them as-is.
        if params is None:
            sql = _sanitize_oracle_sql(sql)
            sql = sql.strip().rstrip(";").rstrip("/").strip().rstrip(";").strip()
            if not sql:
                return None

        try:
            if params:
                self._cursor.execute(sql, list(params))
            else:
                self._cursor.execute(sql)

            sql_upper = sql.upper().lstrip()
            if any(sql_upper.startswith(kw) for kw in ("SELECT", "WITH")):
                cols = [d[0] for d in self._cursor.description]
                rows = self._cursor.fetchall()
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=self._cursor.rowcount or 0, is_select=False)
        except Exception as exc:
            from ..redact import redact as _redact
            safe = _redact(str(exc))
            raise RuntimeError(safe) from None

    def _describe(self, table_name: str) -> QueryResult:
        """Emulate DESCRIBE by querying user_tab_columns."""
        sql = """
            SELECT column_name AS "Name",
                   DECODE(nullable, 'N', 'NOT NULL', '') AS "Null?",
                   data_type ||
                   CASE WHEN data_type IN ('VARCHAR2','CHAR','NVARCHAR2','NCHAR')
                        THEN '(' || data_length || ')'
                        WHEN data_type = 'NUMBER' AND data_precision IS NOT NULL
                        THEN '(' || data_precision ||
                             CASE WHEN data_scale > 0 THEN ',' || data_scale ELSE '' END || ')'
                        ELSE ''
                   END AS "Type"
            FROM user_tab_columns
            WHERE table_name = :tname
            ORDER BY column_id
        """
        try:
            self._cursor.execute(sql, tname=table_name)
            cols = [d[0] for d in self._cursor.description]
            rows = self._cursor.fetchall()
            return QueryResult(columns=cols, rows=rows, rowcount=len(rows),
                               is_select=True, is_describe=True)
        except Exception as exc:
            raise RuntimeError(f"DESCRIBE failed for {table_name}: {exc}") from exc

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            self._conn.rollback()

    def release_savepoint(self, name: str) -> None:
        # Oracle has no RELEASE SAVEPOINT statement — savepoints persist
        # until commit/rollback. Safe no-op.
        pass

    # ── Schema ────────────────────────────────────────────────────────────────

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []

        tables: dict[str, TableSchema] = {}

        # All user tables
        self._cursor.execute(
            "SELECT table_name FROM user_tables ORDER BY table_name"
        )
        for (tname,) in self._cursor.fetchall():
            tables[tname] = TableSchema(name=tname, columns=[])

        # Columns
        self._cursor.execute("""
            SELECT table_name, column_name, data_type,
                   data_length, data_precision, data_scale, nullable
            FROM user_tab_columns
            ORDER BY table_name, column_id
        """)
        for tname, cname, dtype, dlen, dprec, dscale, nullable in self._cursor.fetchall():
            if tname not in tables:
                continue
            ts = dtype
            if dtype in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR") and dlen:
                ts = f"{dtype}({dlen})"
            elif dtype == "NUMBER":
                if dprec and dscale and dscale > 0:
                    ts = f"NUMBER({dprec},{dscale})"
                elif dprec:
                    ts = f"NUMBER({dprec})"
            tables[tname].columns.append(
                ColumnInfo(name=cname, type=ts, nullable=(nullable == "Y"))
            )

        # Primary keys
        self._cursor.execute("""
            SELECT acc.table_name, acc.column_name
            FROM all_constraints ac
            JOIN all_cons_columns acc
              ON ac.constraint_name = acc.constraint_name
             AND ac.owner = acc.owner
            WHERE ac.constraint_type = 'P'
              AND ac.owner = USER
            ORDER BY acc.table_name, acc.position
        """)
        for tname, cname in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.primary_key = True
                        col.nullable = False

        # Foreign keys
        self._cursor.execute("""
            SELECT acc.table_name, acc.column_name,
                   acc2.table_name AS ref_table, acc2.column_name AS ref_col
            FROM all_constraints ac
            JOIN all_cons_columns acc
              ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
            JOIN all_constraints ac2
              ON ac.r_constraint_name = ac2.constraint_name AND ac2.owner = USER
            JOIN all_cons_columns acc2
              ON ac2.constraint_name = acc2.constraint_name AND ac2.owner = acc2.owner
            WHERE ac.constraint_type = 'R'
              AND ac.owner = USER
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
        # Oracle uppercases unquoted, quoted preserves but we upper
        escaped = ident.upper().replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        return "named"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        # Oracle 12c+ supports all
        return feat in {"cte", "window_functions", "returning", "identity", "generated_columns", "check_constraints", "savepoint"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        # Oracle 12c: OFFSET ... ROWS FETCH NEXT ... ROWS ONLY
        parts = []
        if offset is not None:
            parts.append(f"OFFSET {offset} ROWS")
        if limit is not None:
            parts.append(f"FETCH NEXT {limit} ROWS ONLY")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": "RETURNING"}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "SERIALIZABLE"]}


    def discover(self) -> list[str]:
        hints: list[str] = []

        oracle_home = os.environ.get("ORACLE_HOME", "")
        if oracle_home:
            hints.append(f"ORACLE_HOME={oracle_home}")

        tns_admin = os.environ.get("TNS_ADMIN", oracle_home and os.path.join(oracle_home, "network", "admin") or "")
        if tns_admin and os.path.isfile(os.path.join(tns_admin, "tnsnames.ora")):
            hints.append(f"TNS config found at {tns_admin}")
            # Detect RAC entries in tnsnames.ora
            try:
                tns_text = open(os.path.join(tns_admin, "tnsnames.ora"), encoding="utf-8").read().upper()
                if "LOAD_BALANCE" in tns_text or "SCAN" in tns_text or "FAILOVER" in tns_text:
                    hints.append("RAC / SCAN entries detected in tnsnames.ora")
            except Exception:
                pass

        # Check for running oracle process
        try:
            proc = "tasklist" if sys.platform == "win32" else "ps"
            args = [proc, "/FO", "CSV"] if sys.platform == "win32" else [proc, "-e"]
            result = subprocess.run(args, capture_output=True, text=True, timeout=3)
            if "oracle" in result.stdout.lower() or "tnslsnr" in result.stdout.lower():
                hints.append("localhost:1521 (Oracle listener detected)")
            if "crsd" in result.stdout.lower() or "ohasd" in result.stdout.lower():
                hints.append("Oracle Grid / RAC detected (crsd/ohasd running)")
        except Exception:
            pass

        if not hints:
            hints.append("localhost:1521 (default — no running instance detected)")
            hints.append("RAC tip: host='node1,node2' service='racservice' or host='scan-host' for SCAN")

        return hints
