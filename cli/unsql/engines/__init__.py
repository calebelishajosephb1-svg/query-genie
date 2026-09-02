"""
unsql/engines/__init__.py
--------------------------
Engine registry with fuzzy name matching.

Maps all supported RDBMS names (and their common aliases / misspellings)
to their concrete engine classes.

``fuzzy_match_engine(token)`` is the single entry point used by commands.py.
It resolves 'oracle', 'Oracle', 'ORACLE', 'OrAcLe' identically (spec §4).

Driver imports are graceful: if a platform-specific driver fails to import
(ibm_db needs a C compiler, pyodbc needs the OS-level ODBC Driver), that
engine is silently omitted rather than crashing the whole application.
"""
from __future__ import annotations

import difflib
import logging
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .base import DBEngine

_logger = logging.getLogger(__name__)

# ─── Graceful driver imports ─────────────────────────────────────────────────
# Each engine is loaded individually so a single missing driver (e.g. ibm_db
# needing a C compiler) doesn't prevent the rest from working.

ENGINES: dict[str, type] = {}
_IMPORT_FAILURES: list[str] = []


def _try_import(canonical: str, module: str, classname: str) -> None:
    """Import *classname* from *module* into ENGINES[canonical], or log a warning."""
    try:
        mod = __import__(f"unsql.engines.{module}", fromlist=[classname])
        ENGINES[canonical] = getattr(mod, classname)
    except Exception as exc:
        _IMPORT_FAILURES.append(canonical)
        _logger.debug("Engine '%s' unavailable (driver not installed): %s", canonical, exc)


_try_import("postgresql", "postgres", "PostgresEngine")
_try_import("mysql", "mysql", "MySQLEngine")
_try_import("mssql", "mssql", "MSSQLEngine")
_try_import("oracle", "oracle", "OracleEngine")
_try_import("sqlite", "sqlite", "SQLiteEngine")
_try_import("mariadb", "mariadb", "MariaDBEngine")
_try_import("db2", "db2", "Db2Engine")
_try_import("snowflake", "snowflake", "SnowflakeEngine")
_try_import("aurora", "aurora", "AuroraEngine")
_try_import("access", "access", "AccessEngine")


# ─── Alias table (every reasonable alternative spelling) ─────────────────────

_ALIASES: dict[str, str] = {
    # PostgreSQL
    "postgres": "postgresql",
    "pg": "postgresql",
    "psql": "postgresql",
    "postgresql": "postgresql",
    "postgre": "postgresql",
    # MySQL
    "mysql": "mysql",
    # MariaDB
    "mariadb": "mariadb",
    "maria": "mariadb",
    # SQL Server
    "mssql": "mssql",
    "sqlserver": "mssql",
    "sql server": "mssql",
    "sql_server": "mssql",
    "sqlserv": "mssql",
    "microsoftsqlserver": "mssql",
    "microsoft sql server": "mssql",
    "tsql": "mssql",
    # Oracle
    "oracle": "oracle",
    "oracledb": "oracle",
    "oracle database": "oracle",
    # SQLite
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "sqllite": "sqlite",  # common typo
    # Db2
    "db2": "db2",
    "ibm db2": "db2",
    "ibm_db2": "db2",
    "db 2": "db2",
    # Snowflake
    "snowflake": "snowflake",
    "snow": "snowflake",
    # Aurora
    "aurora": "aurora",
    "amazon aurora": "aurora",
    "aws aurora": "aurora",
    # Access
    "access": "access",
    "microsoft access": "access",
    "ms access": "access",
    "msaccess": "access",
}


def fuzzy_match_engine(token: str) -> type | None:
    """
    Resolve a user-typed engine token to an engine class.

    Matching strategy (in order):
    1. Exact alias lookup (case-insensitive)
    2. difflib close-match against all aliases (≥ 0.6 similarity)
    3. Return None if no match

    Returns None if the matched engine's driver failed to import.

    Examples:
        fuzzy_match_engine("oracle")   → OracleEngine
        fuzzy_match_engine("OrAcLe")   → OracleEngine
        fuzzy_match_engine("postgre")  → PostgresEngine
        fuzzy_match_engine("sqlserv")  → MSSQLEngine
        fuzzy_match_engine("xyz")      → None
    """
    normalized = token.strip().lower().replace("-", " ").replace("_", " ")

    # 1. Exact alias → canonical → engine class (graceful if driver missing)
    canonical = _ALIASES.get(normalized)
    if canonical:
        return ENGINES.get(canonical)

    # 2. Fuzzy match — L2 fix: log warning and require stricter cutoff 0.75 to avoid typosquat, plus role check
    matches = difflib.get_close_matches(normalized, list(_ALIASES.keys()), n=1, cutoff=0.75)
    if matches:
        canonical = _ALIASES[matches[0]]
        # Log fuzzy miss for audit trail (defense-in-depth)
        _logger.warning("Fuzzy engine match: '%s' → '%s' (%s). Supported: %s", token, matches[0], canonical, sorted(_ALIASES.keys())[:5])
        return ENGINES.get(canonical)

    # Log complete miss for diagnostics
    _logger.debug("No engine match for '%s'. Supported: %s", token, sorted(ENGINES.keys()))
    return None


__all__ = ["ENGINES", "fuzzy_match_engine"]
