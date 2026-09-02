export type EngineId =
  | "postgresql"
  | "mysql"
  | "mariadb"
  | "mssql"
  | "oracle"
  | "sqlite"
  | "db2"
  | "snowflake"
  | "aurora"
  | "access";

export type Engine = {
  id: EngineId;
  name: string;
  blurb: string;
  ext: string;
};

export const ENGINES: Engine[] = [
  { id: "postgresql", name: "PostgreSQL", blurb: "CTEs, window fns, RETURNING", ext: "sql" },
  { id: "mysql", name: "MySQL 8", blurb: "InnoDB, AUTO_INCREMENT", ext: "sql" },
  { id: "mariadb", name: "MariaDB", blurb: "MySQL-compatible, sequences", ext: "sql" },
  { id: "mssql", name: "SQL Server", blurb: "T-SQL, IDENTITY, GO batches", ext: "sql" },
  { id: "oracle", name: "Oracle", blurb: "SQL*Plus, VARCHAR2, PL/SQL", ext: "sql" },
  { id: "sqlite", name: "SQLite", blurb: "File DB, ISO text dates", ext: "sql" },
  { id: "db2", name: "IBM Db2", blurb: "SYSIBM, FETCH FIRST", ext: "sql" },
  { id: "snowflake", name: "Snowflake", blurb: "QUALIFY, cloud warehouse", ext: "sql" },
  { id: "aurora", name: "Aurora MySQL", blurb: "AWS managed MySQL 8", ext: "sql" },
  { id: "access", name: "MS Access", blurb: "Jet SQL, no CTEs/windows", ext: "sql" },
];

export const engineName = (id: string) => ENGINES.find((e) => e.id === id)?.name ?? id;
