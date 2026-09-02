import { createFileRoute } from "@tanstack/react-router";

type Body = {
  prompt?: string;
  engine?: string;
  model?: string;
  dialectNotes?: string;
};

const GATEWAY = "https://ai.gateway.lovable.dev/v1/chat/completions";

const ENGINE_RULES: Record<string, string> = {
  postgresql:
    "PostgreSQL 16. Use SERIAL/IDENTITY, TEXT/VARCHAR, NUMERIC, TIMESTAMPTZ, RETURNING, CTEs, window functions, FILTER, DISTINCT ON, GENERATED columns. Statement terminator ';'. No PL/SQL blocks unless DO $$.",
  mysql:
    "MySQL 8.0. Use AUTO_INCREMENT, ENGINE=InnoDB, DATETIME, DECIMAL, CTEs and window functions ARE supported in 8.0. No CHECK-less assumptions (8.0.16+ enforces CHECK). No FULL OUTER JOIN (emulate with UNION of LEFT and RIGHT). Use IFNULL/COALESCE, LIMIT.",
  mariadb:
    "MariaDB 10.6+. AUTO_INCREMENT, InnoDB, window functions and CTEs supported. No FULL OUTER JOIN (emulate). SEQUENCE available. Use LIMIT.",
  mssql:
    "Microsoft SQL Server 2019 / T-SQL. IDENTITY(1,1), NVARCHAR, DECIMAL, DATETIME2, GETDATE(), TOP/OFFSET-FETCH, ISNULL, IIF, GO batch separators, DROP TABLE IF EXISTS, MERGE allowed, string concat with +.",
  oracle:
    "Oracle 19c SQL*Plus script. NUMBER/VARCHAR2/DATE, sequences + triggers or GENERATED AS IDENTITY, DUAL, NVL, TO_DATE with explicit format masks, no LIMIT (use FETCH FIRST n ROWS ONLY), '/' after PL/SQL blocks, anonymous BEGIN EXECUTE IMMEDIATE 'DROP TABLE ...' blocks for cleanup, SET LINESIZE/PAGESIZE headers, PROMPT sections. No multi-row VALUES (use INSERT ALL or separate INSERTs).",
  sqlite:
    "SQLite 3.4x. INTEGER PRIMARY KEY AUTOINCREMENT, TEXT dates in ISO format, REAL/NUMERIC, PRAGMA foreign_keys = ON; window functions and CTEs supported. No RIGHT/FULL JOIN in older builds — emulate. No stored procedures. IFNULL, strftime, julianday.",
  db2: "IBM Db2 LUW 11.5. GENERATED ALWAYS AS IDENTITY, VARCHAR, DECIMAL, TIMESTAMP, VALUES clause, SYSIBM.SYSDUMMY1, FETCH FIRST n ROWS ONLY, COALESCE, terminator ';' with @ for compound statements.",
  snowflake:
    "Snowflake. Use CREATE OR REPLACE TABLE, NUMBER/VARCHAR/TIMESTAMP_NTZ, AUTOINCREMENT / IDENTITY, no enforced foreign keys (declare them but note they are informational), QUALIFY clause, generous window functions, IFF, ZEROIFNULL, multi-row VALUES supported.",
  aurora:
    "Amazon Aurora MySQL-compatible (8.0). Treat as MySQL 8.0 but note Aurora-specific caveats where relevant (no LOCAL INFILE assumptions, read-replica considerations). AUTO_INCREMENT, InnoDB.",
  access:
    "Microsoft Access (Jet/ACE SQL). VERY limited: AUTOINCREMENT, TEXT(n), CURRENCY, DATETIME, no CTEs, no window functions, no FULL OUTER JOIN, INNER/LEFT/RIGHT JOIN require parentheses for multi-joins, IIF/NZ instead of CASE-COALESCE where clearer, TOP n instead of LIMIT, date literals wrapped in #...#, one statement per query object, subqueries allowed. Emulate ranking/percentages with correlated subqueries and saved-query style stepwise SQL, and explain each workaround in comments.",
};

function systemPrompt(engine: string, notes: string) {
  const rules = ENGINE_RULES[engine] ?? engine;
  return `You are a senior database engineer who writes production-grade, runnable SQL scripts.

TARGET ENGINE: ${engine.toUpperCase()}
DIALECT RULES (must be obeyed strictly): ${rules}
${notes ? `EXTRA USER CONSTRAINTS: ${notes}` : ""}

Hard requirements:
- Output ONE single script that can be pasted into the engine's client and run top to bottom, in dependency-safe order.
- Every statement must be valid for the target dialect only. Never emit syntax from another engine.
- If the dialect cannot express something (window functions, CTEs, FULL OUTER JOIN, etc.), emulate it and add a comment explaining the workaround.
- Cover: cleanup/drop, DDL with keys and constraints, realistic seed data (including deliberate orphan / NULL / tie / empty cases when asked), then every requested query in the requested order, then DML changes with before/after selects, then deletes with verification selects, then the final full-table dumps.
- Number and label sections with comments so the script reads like a report.
- Never truncate, never write "-- ... rest omitted", never summarise instead of writing SQL. Completeness matters more than brevity.
- Respond with the SQL script only: no markdown fences, no prose outside SQL comments.`;
}

async function callGateway(
  apiKey: string,
  model: string,
  messages: Array<{ role: string; content: string }>,
  stream: boolean,
) {
  return fetch(GATEWAY, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model, messages, stream }),
  });
}

export const Route = createFileRoute("/api/generate")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const apiKey = process.env["LOVABLE_API_KEY"];
        if (!apiKey) {
          return new Response(JSON.stringify({ error: "AI key not configured" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          });
        }

        let body: Body;
        try {
          body = (await request.json()) as Body;
        } catch {
          return new Response(JSON.stringify({ error: "Invalid JSON body" }), { status: 400 });
        }

        const prompt = (body.prompt ?? "").trim();
        const engine = (body.engine ?? "postgresql").toLowerCase();
        const notes = (body.dialectNotes ?? "").trim();
        const model = body.model === "flash" ? "google/gemini-2.5-flash" : "google/gemini-2.5-pro";

        if (!prompt) {
          return new Response(JSON.stringify({ error: "Prompt is required" }), { status: 400 });
        }
        if (prompt.length > 40000) {
          return new Response(JSON.stringify({ error: "Prompt too long" }), { status: 400 });
        }

        const encoder = new TextEncoder();
        const stream = new ReadableStream({
          async start(controller) {
            const send = (event: string, data: unknown) => {
              controller.enqueue(
                encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
              );
            };

            try {
              // ---------- Phase 1: reasoning / plan ----------
              send("phase", { phase: "plan" });
              const planRes = await callGateway(
                apiKey,
                model,
                [
                  {
                    role: "system",
                    content: `You are a meticulous database architect. Before any SQL is written, you produce an implementation plan for the target engine ${engine.toUpperCase()}.
Dialect rules: ${ENGINE_RULES[engine] ?? engine}
Write a compact but COMPLETE plan in plain text:
1. Every table, its columns/types/keys and the relationships between them.
2. The seed-data strategy, including which rows are intentionally orphaned / NULL / tied / empty.
3. An ordered checklist of EVERY question or operation the user asked for, one line each, in the user's original order, noting for each which dialect feature or workaround you will use.
4. Dialect traps to avoid for this engine.
Do not write the SQL yet. No markdown fences.`,
                  },
                  { role: "user", content: prompt },
                ],
                true,
              );

              if (!planRes.ok || !planRes.body) {
                const text = await planRes.text().catch(() => "");
                send("error", {
                  message:
                    planRes.status === 429
                      ? "Rate limit reached, please retry shortly."
                      : planRes.status === 402
                        ? "AI credits exhausted. Add credits in Settings → Workspace → Usage."
                        : `AI request failed (${planRes.status}). ${text.slice(0, 200)}`,
                });
                controller.close();
                return;
              }

              const readSSE = async (res: Response, onDelta: (t: string) => void) => {
                const reader = res.body!.getReader();
                const dec = new TextDecoder();
                let buf = "";
                let full = "";
                for (;;) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  buf += dec.decode(value, { stream: true });
                  let nl: number;
                  while ((nl = buf.indexOf("\n")) !== -1) {
                    const line = buf.slice(0, nl).trim();
                    buf = buf.slice(nl + 1);
                    if (!line.startsWith("data:")) continue;
                    const payload = line.slice(5).trim();
                    if (payload === "[DONE]") continue;
                    try {
                      const json = JSON.parse(payload);
                      const delta = json.choices?.[0]?.delta?.content;
                      if (typeof delta === "string" && delta) {
                        full += delta;
                        onDelta(delta);
                      }
                    } catch {
                      /* partial chunk, ignore */
                    }
                  }
                }
                return full;
              };

              const plan = await readSSE(planRes, (t) => send("plan", { text: t }));

              // ---------- Phase 2: SQL generation ----------
              send("phase", { phase: "sql" });
              const sqlRes = await callGateway(
                apiKey,
                model,
                [
                  { role: "system", content: systemPrompt(engine, notes) },
                  { role: "user", content: prompt },
                  {
                    role: "assistant",
                    content: `Here is my implementation plan before writing SQL:\n\n${plan}`,
                  },
                  {
                    role: "user",
                    content: `Good. Now write the FULL ${engine.toUpperCase()} script following that plan exactly, in order, with nothing omitted. SQL only.`,
                  },
                ],
                true,
              );

              if (!sqlRes.ok || !sqlRes.body) {
                const text = await sqlRes.text().catch(() => "");
                send("error", {
                  message:
                    sqlRes.status === 429
                      ? "Rate limit reached, please retry shortly."
                      : sqlRes.status === 402
                        ? "AI credits exhausted. Add credits in Settings → Workspace → Usage."
                        : `AI request failed (${sqlRes.status}). ${text.slice(0, 200)}`,
                });
                controller.close();
                return;
              }

              await readSSE(sqlRes, (t) => send("sql", { text: t }));
              send("done", { ok: true });
            } catch (err) {
              send("error", { message: err instanceof Error ? err.message : "Unexpected error" });
            } finally {
              controller.close();
            }
          },
        });

        return new Response(stream, {
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            Connection: "keep-alive",
          },
        });
      },
    },
  },
});
