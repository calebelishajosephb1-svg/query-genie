"""
unsql/prompts.py
----------------
Dialect rules + the prompt builders used by the always-on agent loop
(plan pass, SQL pass, static-review pass, self-repair pass).

UNSQL has no engine and no storage of its own: every prompt here is grounded
in the LIVE schema of the connected database.
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
        "TOP / OFFSET-FETCH, ISNULL, IIF, DROP TABLE IF EXISTS, MERGE, string concat with +. "
        "Never emit GO — statements are executed one at a time over a live connection."
    ),
    "oracle": (
        "Oracle 19c. NUMBER/VARCHAR2/DATE, sequences+triggers or GENERATED AS IDENTITY, DUAL, NVL, "
        "TO_DATE with explicit format masks, no LIMIT (FETCH FIRST n ROWS ONLY). "
        "No multi-row VALUES — use INSERT ALL. No SQL*Plus directives (SET LINESIZE, SPOOL, '/'): "
        "statements run through a driver, not SQL*Plus."
    ),
    "sqlite": (
        "SQLite 3.4x. INTEGER PRIMARY KEY AUTOINCREMENT, ISO TEXT dates, REAL/NUMERIC, "
        "PRAGMA foreign_keys = ON; window functions and CTEs supported. No stored procedures. "
        "Avoid RIGHT/FULL JOIN — emulate. IFNULL, strftime, julianday."
    ),
    "db2": (
        "IBM Db2 LUW 11.5. GENERATED ALWAYS AS IDENTITY, VARCHAR, DECIMAL, TIMESTAMP, VALUES clause, "
        "SYSIBM.SYSDUMMY1, FETCH FIRST n ROWS ONLY, COALESCE."
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
        "TOP n instead of LIMIT, date literals in #...#, one statement per query object."
    ),
}


def rules_for(engine: str) -> str:
    return ENGINE_RULES.get(engine, engine)


_COMMON = """Hard rules:
- Every statement must be valid for the target dialect only, and must run over a live driver
  connection (no client directives, no batch separators, no interactive commands).
- Emit statements in dependency-safe order: parents before children for creates and inserts,
  children before parents for deletes and drops.
- Use only tables and columns that exist in the live schema, spelled exactly as shown, unless
  the request asks you to create them.
- Never truncate, never write "-- rest omitted", never summarise instead of writing SQL.
- Respond with SQL only: no markdown fences, no prose outside SQL comments."""


def plan_system(engine: str, schema: str) -> str:
    return f"""You are a meticulous database architect working on a LIVE {engine.upper()} database.
Dialect rules: {rules_for(engine)}

LIVE SCHEMA (current state of the connected database):
{schema or "(empty database — no tables yet)"}

Produce a compact but COMPLETE execution plan in plain text, no SQL:
1. Objects that must exist, their columns/types/keys and relationships.
2. Seed-data strategy, including rows intentionally orphaned / NULL / tied / empty.
3. An ordered checklist of EVERY question or operation the user asked for, one line each, in the
   user's original order, noting the dialect feature or workaround used for each.
4. Dependency order for inserts, updates, deletes and drops.
5. Dialect traps to avoid on this engine."""


def sql_system(engine: str, schema: str, plan: str = "") -> str:
    return f"""You translate natural language into {engine.upper()} SQL that is executed
immediately against a live database in the same session.

DIALECT RULES: {rules_for(engine)}

LIVE SCHEMA:
{schema or "(empty database — no tables yet)"}
{f"AGREED PLAN (follow it exactly):{chr(10)}{plan}" if plan else ""}

{_COMMON}
- Label sections with SQL comments so the run reads like a report.
- Include the verification / before-after SELECTs the request implies."""


def review_system(engine: str, schema: str) -> str:
    return f"""You are a SQL reviewer for {engine.upper()}. You are given a script that is about to
be executed against a live database.

DIALECT RULES: {rules_for(engine)}
LIVE SCHEMA:
{schema or "(empty database — no tables yet)"}

Check for: dialect syntax errors, unknown tables/columns, constraint and foreign-key violations,
wrong statement order (child before parent on insert, parent before child on delete/drop),
missing objects, and requested steps that are missing entirely.

If everything is correct, reply with exactly: OK
Otherwise reply with the FULL corrected script (SQL only, no fences, no prose outside comments)."""


def repair_system(engine: str, schema: str) -> str:
    return f"""You fix a single failing {engine.upper()} statement mid-run against a live database.

DIALECT RULES: {rules_for(engine)}
LIVE SCHEMA:
{schema or "(empty database — no tables yet)"}

You get the statement and the exact engine error. Reply with the corrected SQL for that step only
(one or more statements). If the step is impossible and must be skipped, reply with exactly: SKIP
SQL only: no fences, no prose outside SQL comments."""


def strip_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1] if "\n" in out else ""
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    return out.strip()
