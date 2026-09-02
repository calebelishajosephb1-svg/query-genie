"""
unsql/prompts.py
----------------
Dialect rules + the two prompt builders: a reasoning pass and a SQL pass.
"""
from __future__ import annotations

ENGINE_RULES: dict[str, str] = {
    "postgresql": (
        "PostgreSQL 16. SERIAL/IDENTITY, TEXT/VARCHAR, NUMERIC, TIMESTAMPTZ, RETURNING, CTEs, "
        "window functions, FILTER, DISTINCT ON, GENERATED columns. Terminator ';'. "
        "No PL/SQL blocks unless DO $$."
    ),
    "mysql": (
        "MySQL 8.0. AUTO_INCREMENT, ENGINE=InnoDB, DATETIME, DECIMAL. CTEs and window functions "
        "are supported. CHECK is enforced (8.0.16+). No FULL OUTER JOIN — emulate with UNION of "
        "LEFT and RIGHT. IFNULL/COALESCE, LIMIT."
    ),
    "mariadb": (
        "MariaDB 10.6+. AUTO_INCREMENT, InnoDB, window functions and CTEs supported. "
        "No FULL OUTER JOIN — emulate. SEQUENCE available. LIMIT."
    ),
    "mssql": (
        "Microsoft SQL Server 2019 / T-SQL. IDENTITY(1,1), NVARCHAR, DECIMAL, DATETIME2, GETDATE(), "
        "TOP / OFFSET-FETCH, ISNULL, IIF, GO batch separators, DROP TABLE IF EXISTS, MERGE, "
        "string concat with +."
    ),
    "oracle": (
        "Oracle 19c SQL*Plus script. NUMBER/VARCHAR2/DATE, sequences+triggers or GENERATED AS "
        "IDENTITY, DUAL, NVL, TO_DATE with explicit format masks, no LIMIT (FETCH FIRST n ROWS "
        "ONLY), '/' after PL/SQL blocks, anonymous BEGIN EXECUTE IMMEDIATE 'DROP TABLE ...' for "
        "cleanup, SET LINESIZE/PAGESIZE headers. No multi-row VALUES — use INSERT ALL."
    ),
    "sqlite": (
        "SQLite 3.4x. INTEGER PRIMARY KEY AUTOINCREMENT, ISO TEXT dates, REAL/NUMERIC, "
        "PRAGMA foreign_keys = ON; window functions and CTEs supported. No stored procedures. "
        "Avoid RIGHT/FULL JOIN — emulate. IFNULL, strftime, julianday."
    ),
    "db2": (
        "IBM Db2 LUW 11.5. GENERATED ALWAYS AS IDENTITY, VARCHAR, DECIMAL, TIMESTAMP, VALUES clause, "
        "SYSIBM.SYSDUMMY1, FETCH FIRST n ROWS ONLY, COALESCE, ';' terminator (@ for compound)."
    ),
    "snowflake": (
        "Snowflake. CREATE OR REPLACE TABLE, NUMBER/VARCHAR/TIMESTAMP_NTZ, AUTOINCREMENT, foreign "
        "keys are informational only, QUALIFY, rich window functions, IFF, ZEROIFNULL, multi-row "
        "VALUES supported."
    ),
    "aurora": (
        "Amazon Aurora MySQL-compatible (8.0). Treat as MySQL 8.0; AUTO_INCREMENT, InnoDB, no "
        "LOCAL INFILE assumptions."
    ),
    "access": (
        "Microsoft Access (Jet/ACE SQL). Very limited: AUTOINCREMENT, TEXT(n), CURRENCY, DATETIME, "
        "no CTEs, no window functions, no FULL OUTER JOIN, multi-joins need parentheses, IIF/NZ, "
        "TOP n instead of LIMIT, date literals in #...#, one statement per query object. Emulate "
        "ranking/percentages with correlated subqueries and explain each workaround in comments."
    ),
}


def rules_for(engine: str) -> str:
    return ENGINE_RULES.get(engine, engine)


def plan_system(engine: str) -> str:
    return f"""You are a meticulous database architect working against {engine.upper()}.
Dialect rules: {rules_for(engine)}

Before any SQL is written, produce a compact but COMPLETE plan in plain text:
1. Every table, its columns/types/keys and the relationships between them.
2. The seed-data strategy, including which rows are intentionally orphaned / NULL / tied / empty.
3. An ordered checklist of EVERY question or operation the user asked for, one line each, in the
   user's original order, noting the dialect feature or workaround used for each.
4. Dialect traps to avoid for this engine.
Do not write SQL yet. No markdown fences."""


def script_system(engine: str, notes: str = "", schema: str = "") -> str:
    return f"""You are a senior database engineer who writes production-grade, runnable SQL.

TARGET ENGINE: {engine.upper()}
DIALECT RULES (obey strictly): {rules_for(engine)}
{f"EXTRA USER CONSTRAINTS: {notes}" if notes else ""}
{f"LIVE DATABASE SCHEMA (use these exact table/column names):{chr(10)}{schema}" if schema else ""}

Hard requirements:
- Output ONE script that runs top to bottom in dependency-safe order.
- Every statement must be valid for the target dialect only. Never emit another engine's syntax.
- If the dialect cannot express something, emulate it and add a comment explaining the workaround.
- Cover, when asked for: cleanup/drop, DDL with keys and constraints, realistic seed data
  (including deliberate orphan / NULL / tie / empty cases), every requested query in the requested
  order, DML changes with before/after selects, deletes with verification selects, final dumps.
- Number and label sections with comments so the script reads like a report.
- Never truncate, never write "-- rest omitted", never summarise instead of writing SQL.
- Respond with SQL only: no markdown fences, no prose outside SQL comments."""


def live_system(engine: str, schema: str) -> str:
    return f"""You translate natural language into {engine.upper()} SQL to run against a live database.

DIALECT RULES: {rules_for(engine)}

LIVE SCHEMA:
{schema or "(schema unavailable — ask the user to create the objects first)"}

Rules:
- Use only tables and columns that exist in the schema above, spelled exactly as shown.
- Emit exactly the statements needed to answer the request, in dependency-safe order.
- Respond with SQL only: no markdown fences, no prose outside SQL comments."""


def strip_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1] if "\n" in out else ""
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    return out.strip()
