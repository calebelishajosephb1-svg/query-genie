"""
unsql/engines/base.py
---------------------
Abstract base class for every RDBMS engine adapter.

Every concrete engine (Oracle, PostgreSQL, MySQL, ...) must subclass DBEngine
and implement all abstractmethods so the rest of UNSQL can talk to any engine
through one uniform interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ColumnInfo:
    """Metadata for a single column in a table."""

    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str | None = None  # "referenced_table.referenced_column"


@dataclass
class TableSchema:
    """Schema representation of a single database table."""

    name: str
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass
class QueryResult:
    """
    The result of executing one SQL statement.

    Attributes
    ----------
    columns   : List of column header strings.
    rows      : List of tuples — one per result row.
    rowcount  : Rows affected (DML) or returned (SELECT).
    is_select : True for SELECT / SHOW / PRAGMA queries that return rows.
    is_describe: True when the result is a DESCRIBE-style schema dump;
                 the formatter will use a different layout.
    """

    columns: list[str]
    rows: list[tuple[Any, ...]]
    rowcount: int = 0
    is_select: bool = True
    is_describe: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class DBEngine(ABC):
    """
    Uniform interface that every RDBMS adapter must implement.

    UNSQL never imports a concrete engine class directly — it always works
    through this interface, so swapping engines is transparent.
    """

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Full human-readable name, e.g. 'Oracle Database'."""

    @property
    @abstractmethod
    def engine_type(self) -> str:
        """
        Short lower-case identifier used in AI prompts and formatter
        selection, e.g. 'oracle', 'postgresql', 'mysql'.
        """

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        """
        Open a live session to the engine.

        Raises
        ------
        ConnectionError
            With a clear human-readable message on any connection failure.
        RuntimeError
            If the driver package is not installed on this system.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Close the current session cleanly. No-op if already disconnected."""

    def is_connected(self) -> bool:
        """Return True if there is an active, usable session."""
        return False

    # ── Capabilities ──────────────────────────────────────────────────────────

    @property
    def paramstyle(self) -> str | None:
        """
        Driver parameter style for bound queries: "qmark" (?), "numeric"
        (:1), "format" (%s) — or None when this adapter does not accept
        bound parameters. Only engines declaring a style participate in
        literal-to-bind rewriting (core/binder.py).
        """
        return None

    @property
    def ddl_auto_commits(self) -> bool:
        """
        True when the engine implicitly COMMITs around DDL statements
        (Oracle, MySQL, Snowflake, DB2). On such engines a plan mixing DDL
        and DML can never be atomic: UNSQL commits explicitly at each DDL
        boundary instead of pretending rollback-everything is possible.
        Transactional-DDL engines (PostgreSQL, SQLite) return False.
        """
        return False

    # ── Robust Execution & Idempotency ────────────────────────────────────────

    # NOTE: lazily initialised per INSTANCE. A class-level dict here would be
    # shared across every engine instance in the process — one connection's
    # cached results silently served to another.
    _idempotency_cache: dict[str, QueryResult | None] | None = None

    def _idem_cache(self) -> dict[str, QueryResult | None]:
        if self._idempotency_cache is None:
            self._idempotency_cache = {}
        return self._idempotency_cache

    def clear_idempotency_cache(self) -> None:
        """Clear the idempotency cache, typically at the start of a new session or compound prompt."""
        self._idem_cache().clear()

    def execute_with_idempotency(
        self,
        sql: str,
        idempotency_key: str | None = None,
        params: list | tuple | None = None,
    ) -> QueryResult | None:
        """
        Wrapper to enforce idempotency metadata tags for retries so we don't duplicate state on partial failure.

        The key MUST uniquely identify the logical statement (content-hash),
        not just its position in a plan — step indices restart at 0 every
        turn, so positional keys collide across turns and serve stale
        cached results instead of executing.
        """
        cache = self._idem_cache()
        if idempotency_key and idempotency_key in cache:
            return cache[idempotency_key]

        # Only hand params to engines that opted into them; adapters without
        # a `params` kwarg keep their original signature and behavior.
        if params:
            result = self.execute(sql, params)
        else:
            result = self.execute(sql)
        if idempotency_key:
            cache[idempotency_key] = result
        return result

    def transaction_block(self):
        """Context manager for safer transaction boundaries."""
        from contextlib import contextmanager
        @contextmanager
        def _txn():
            try:
                yield
                try:
                    self.commit()
                except Exception:
                    try:
                        self.rollback()
                    except Exception:
                        pass
                    raise
            except Exception:
                try:
                    self.rollback()
                except Exception:
                    pass
                raise
        return _txn()

    # ── Execution ─────────────────────────────────────────────────────────────

    @abstractmethod
    def execute(self, sql: str) -> QueryResult | None:
        """
        Execute one SQL statement.

        Handling by statement type
        --------------------------
        SELECT / WITH / SHOW / PRAGMA  →  QueryResult(is_select=True, rows=[...])
        DESCRIBE / DESC <table>        →  QueryResult(is_describe=True, rows=[...])
        DDL / DML                      →  QueryResult(is_select=False, rowcount=N)

        Raises
        ------
        RuntimeError
            On any SQL execution error; the caller is responsible for rollback.
        """

    def executemany(self, sql: str, params_seq: list[list | tuple]) -> QueryResult | None:
        """
        Execute one SQL statement against a sequence of parameter tuples.
        Default implementation iterates and executes one by one.
        Concrete engines may override for performance.
        """
        rowcount = 0
        for params in params_seq:
            res = self.execute(sql, params)
            if res:
                rowcount += res.rowcount
        return QueryResult(columns=[], rows=[], rowcount=rowcount, is_select=False)

    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the current transaction."""

    def get_query_cost(self, sql: str) -> float | None:
        """
        Run an EXPLAIN query to get the estimated cost.
        Return a float if supported and parsed, else None.
        """
        return None

    # ── Savepoints (deterministic undo / partial-plan recovery) ──────────────

    def supports_savepoints(self) -> bool:
        """
        True if this engine supports SAVEPOINT / ROLLBACK TO SAVEPOINT.
        Engines without transactional savepoints (e.g. Snowflake, Access)
        override this to return False; callers fall back to full rollback.
        """
        return True

    def savepoint(self, name: str) -> None:
        """Create a named savepoint in the current transaction."""
        if self.ddl_auto_commits:
            # DDL auto-commit engines invalidate savepoints around DDL — caller must commit at DDL boundaries (B3)
            return
        if not self.supports_savepoints():
            return
        self.execute(f"SAVEPOINT {name}")

    def rollback_to_savepoint(self, name: str) -> None:
        """Roll back the current transaction to the named savepoint."""
        if self.ddl_auto_commits or not self.supports_savepoints():
            # Fall back to full rollback on auto-commit engines
            self.rollback()
            return
        self.execute(f"ROLLBACK TO SAVEPOINT {name}")

    def release_savepoint(self, name: str) -> None:
        """Release (forget) a named savepoint. No-op if unsupported."""
        if self.ddl_auto_commits or not self.supports_savepoints():
            return
        try:
            self.execute(f"RELEASE SAVEPOINT {name}")
        except Exception:
            pass  # Some engines (Oracle) have no RELEASE — harmless

    # ── Schema introspection ──────────────────────────────────────────────────

    # -- Dialect Abstraction (Phase 03) -----------------------------------

    # The following provide dialect-specific SQL/identifier handling.
    # Default implementations delegate to get_schema or provide generic fallback;
    # concrete engines override for correctness.

    def list_tables(self) -> list[str]:
        try:
            return [t.name for t in self.get_schema()]
        except Exception:
            return []

    def describe_table(self, table: str):
        for t in self.get_schema():
            if t.name.upper() == table.upper():
                return t
        return None

    def get_columns(self, table: str) -> list:
        t = self.describe_table(table)
        return list(t.columns) if t else []

    def get_constraints(self, table: str) -> list[dict]:
        return []

    def get_foreign_keys(self, table: str) -> list[dict]:
        t = self.describe_table(table)
        if not t:
            return []
        fks = []
        for c in t.columns:
            if c.foreign_key:
                parts = c.foreign_key.split(".")
                fks.append({"column": c.name, "ref_table": parts[0] if len(parts)>1 else c.foreign_key, "ref_column": parts[1] if len(parts)>1 else "", "name": None})
        return fks

    def get_indexes(self, table: str) -> list[dict]:
        return []

    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            parts = ident.split(".")
            return ".".join(self.quote_identifier(p) for p in parts)
        escaped = ident.replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        ps = self.paramstyle
        if ps:
            return ps
        return "qmark"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        defaults = {
            "cte": True,
            "window_functions": True,
            "returning": False,
            "identity": False,
            "generated_columns": False,
            "check_constraints": True,
            "savepoint": True,
        }
        return defaults.get(feat, False)

    def pagination_syntax(self, limit: int | None = None, offset: int | None = None) -> str:
        lim = f"LIMIT {limit}" if limit is not None else ""
        off = f"OFFSET {offset}" if offset is not None else ""
        return f"{lim} {off}".strip()

    def identity_behavior(self) -> dict:
        return {"supports_identity": False, "insert_omit": False, "returning_clause": None}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": self.supports_savepoints(), "ddl_transactional": not self.ddl_auto_commits, "isolation_levels": ["READ_COMMITTED"]}

    # -- Schema introspection --

    @abstractmethod
    def get_schema(self) -> list[TableSchema]:
        """
        Return the live schema of the connected database.

        Called after every write operation so the AI always has current
        context and never hallucinates tables or columns.
        """

    def schema_to_text(self, nl_input: str = "", tables: "list[TableSchema] | None" = None) -> str:
        """
        Serialize the current schema as plain text for the AI system prompt.
        If nl_input is provided, filters detailed column info to only tables
        that plausibly match words in the input.

        Parameters
        ----------
        tables : list[TableSchema] | None
            If the caller already fetched the schema this turn (e.g. for
            deterministic intent routing), pass it here to avoid a second
            round-trip to the database. When None, this method fetches it.

        Example output
        --------------
        TABLE EMPLOYEES:
          EMP_ID NUMBER NOT NULL [PK]
          NAME VARCHAR2(100) NULLABLE
          DEPT_ID NUMBER NULLABLE [FK -> DEPARTMENTS.DEPT_ID]

        TABLE DEPARTMENTS: (columns omitted for brevity)
        """
        if tables is None:
            try:
                tables = self.get_schema()
            except Exception:
                return "(schema unavailable)"

        if not tables:
            return "(empty database — no tables yet)"

        matched_tables = set()
        if nl_input:
            import difflib
            import re
            
            input_lower = nl_input.lower()
            words = set(re.findall(r'\b\w+\b', input_lower))
            
            for t in tables:
                name_lower = t.name.lower()
                # Check exact substring
                if name_lower in input_lower or name_lower.rstrip('s') in input_lower:
                    matched_tables.add(t.name)
                    continue
                
                # Check fuzzy match against words for table name
                if any(difflib.SequenceMatcher(None, name_lower, w).ratio() > 0.75 for w in words):
                    matched_tables.add(t.name)
                    continue
                    
                # Check fuzzy match against column names
                for c in t.columns:
                    col_lower = c.name.lower()
                    if col_lower in input_lower or any(difflib.SequenceMatcher(None, col_lower, w).ratio() > 0.8 for w in words):
                        matched_tables.add(t.name)
                        break
        
        # If nothing matched (e.g. empty input or vague prompt), show all tables
        # But if the schema is huge (enterprise DB), cap detailed column dumps to 35 tables
        _MAX_DETAILED_TABLES = 35
        if not matched_tables:
            matched_tables = {t.name for t in tables}

        lines: list[str] = []
        detailed_count = 0
        omitted_count = 0

        for t in tables:
            if t.name in matched_tables and (len(matched_tables) <= _MAX_DETAILED_TABLES or detailed_count < _MAX_DETAILED_TABLES):
                lines.append(f"TABLE {t.name}:")
                for c in t.columns:
                    null_str = "NOT NULL" if not c.nullable else "NULLABLE"
                    pk_str = " [PK]" if c.primary_key else ""
                    fk_str = f" [FK -> {c.foreign_key}]" if c.foreign_key else ""
                    lines.append(f"  {c.name} {c.type} {null_str}{pk_str}{fk_str}")
                lines.append("")
                detailed_count += 1
            else:
                lines.append(f"TABLE {t.name}: (columns omitted for brevity)\n")
                omitted_count += 1

        if omitted_count > 0 and len(matched_tables) > _MAX_DETAILED_TABLES:
            lines.append(
                f"[Note: Large schema detected — showing {detailed_count} of {len(tables)} tables with full column details. "
                "Mention specific table names in your query for their complete column schema.]"
            )

        from ..core.topology import inject_topology_hints
        topology_text = inject_topology_hints(tables, matched_tables)
        if topology_text:
            lines.append(topology_text)

        # Inject semantic dictionary mappings if present
        from ..core.security import SecurityContext
        policy = SecurityContext.get_policy()
        if policy and getattr(policy, "semantic_dictionary_path", None):
            dict_path = policy.semantic_dictionary_path
            try:
                import os, yaml
                if os.path.exists(dict_path):
                    with open(dict_path, 'r', encoding='utf-8') as f:
                        semantic_data = yaml.safe_load(f)
                    if semantic_data:
                        lines.append("\n=== SEMANTIC BUSINESS DICTIONARY ===")
                        lines.append("Use the following definitions to map business concepts to the schema:")
                        for key, value in semantic_data.items():
                            lines.append(f"- {key}: {value}")
                        lines.append("=====================================")
            except Exception:
                pass

        return "\n".join(lines).rstrip()

    # ── Discovery ─────────────────────────────────────────────────────────────

    @abstractmethod
    def discover(self) -> list[str]:
        """
        Scan the local system for running / available instances of this engine.

        Returns a list of human-readable connection hints
        (e.g. 'localhost:5432', '/path/to/file.db').
        An empty list means nothing was auto-detected — not an error.
        """
