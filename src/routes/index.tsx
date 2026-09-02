import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  Copy,
  Download,
  Database,
  Loader2,
  Sparkles,
  Square,
  Terminal,
  Brain,
} from "lucide-react";
import { toast } from "sonner";

import { EnginePicker } from "@/components/EnginePicker";
import { SAMPLE_PROMPT } from "@/lib/sample-prompt";
import { engineName, type EngineId } from "@/lib/engines";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Dialect — Natural Language to SQL for 10 Database Engines" },
      {
        name: "description",
        content:
          "Describe a database and the queries you want in plain English. Dialect thinks it through, then writes a full runnable SQL script for PostgreSQL, MySQL, Oracle, SQL Server, SQLite and more.",
      },
      { property: "og:title", content: "Dialect — Natural Language to SQL" },
      {
        property: "og:description",
        content:
          "Plain-English database requests turned into complete, engine-specific SQL scripts you can download and run.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

type Phase = "idle" | "plan" | "sql" | "done" | "error";

function Index() {
  const [prompt, setPrompt] = useState("");
  const [engine, setEngine] = useState<EngineId>("postgresql");
  const [model, setModel] = useState<"pro" | "flash">("pro");
  const [notes, setNotes] = useState("");
  const [plan, setPlan] = useState("");
  const [sql, setSql] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const sqlRef = useRef<HTMLPreElement>(null);
  const planRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (phase === "sql" && sqlRef.current) sqlRef.current.scrollTop = sqlRef.current.scrollHeight;
  }, [sql, phase]);
  useEffect(() => {
    if (phase === "plan" && planRef.current)
      planRef.current.scrollTop = planRef.current.scrollHeight;
  }, [plan, phase]);

  const running = phase === "plan" || phase === "sql";

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase(sql ? "done" : "idle");
  };

  const generate = async () => {
    if (!prompt.trim()) {
      toast.error("Describe what you want first.");
      return;
    }
    setPlan("");
    setSql("");
    setError("");
    setPhase("plan");
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, engine, model, dialectNotes: notes }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        const msg = await res.text().catch(() => "");
        throw new Error(msg || `Request failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() ?? "";
        for (const frame of frames) {
          const evLine = frame.split("\n").find((l) => l.startsWith("event:"));
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!evLine || !dataLine) continue;
          const ev = evLine.slice(6).trim();
          let data: { text?: string; phase?: string; message?: string };
          try {
            data = JSON.parse(dataLine.slice(5).trim());
          } catch {
            continue;
          }
          if (ev === "phase" && data.phase === "sql") setPhase("sql");
          else if (ev === "plan") setPlan((p) => p + (data.text ?? ""));
          else if (ev === "sql") setSql((s) => s + (data.text ?? ""));
          else if (ev === "error") {
            setError(data.message ?? "Something went wrong");
            setPhase("error");
          } else if (ev === "done") setPhase("done");
        }
      }
      setPhase((p) => (p === "error" ? p : "done"));
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setError(e instanceof Error ? e.message : "Unexpected error");
      setPhase("error");
    } finally {
      abortRef.current = null;
    }
  };

  const cleanSql = sql.replace(/^```[a-z]*\n?/i, "").replace(/```$/i, "");

  const download = () => {
    const blob = new Blob([cleanSql], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${engine}-script.txt`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Script downloaded");
  };

  const copy = async () => {
    await navigator.clipboard.writeText(cleanSql);
    toast.success("SQL copied to clipboard");
  };

  const lines = cleanSql ? cleanSql.split("\n").length : 0;

  return (
    <main className="min-h-screen grid-lines">
      <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6 lg:py-14">
        <header className="mb-10">
          <div className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1 font-mono text-[11px] tracking-wide text-muted-foreground">
            <Database className="h-3.5 w-3.5 text-primary" />
            10 ENGINES · REASONED · DOWNLOADABLE
          </div>
          <h1 className="mt-5 font-display text-4xl font-bold tracking-tight sm:text-6xl">
            Natural language,
            <span className="block text-primary">compiled to SQL.</span>
          </h1>
          <p className="mt-4 max-w-2xl text-base text-muted-foreground">
            Describe the schema, the seed data and every query you want — however messy. The model
            plans the whole thing first, then writes one complete, runnable script in the dialect
            you pick.
          </p>
        </header>

        <section className="panel p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label htmlFor="req" className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
              01 · Your request
            </label>
            <button
              type="button"
              onClick={() => setPrompt(SAMPLE_PROMPT)}
              className="rounded-md border border-border bg-surface-2 px-2.5 py-1 font-mono text-[11px] text-accent transition-colors hover:border-accent/50"
            >
              load restaurant-chain example
            </button>
          </div>
          <textarea
            id="req"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={running}
            rows={10}
            placeholder="e.g. build a restaurant chain database with branches, staff, menus, orders, payments… then rank employees per branch by revenue handled…"
            className="mt-3 w-full resize-y rounded-lg border border-input bg-background/60 p-4 font-mono text-[13px] leading-relaxed text-foreground outline-none transition-shadow placeholder:text-muted-foreground/60 focus:glow-ring disabled:opacity-60"
          />

          <div className="mt-6">
            <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
              02 · Target engine
            </span>
            <div className="mt-3">
              <EnginePicker value={engine} onChange={setEngine} disabled={running} />
            </div>
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
            <div>
              <label
                htmlFor="notes"
                className="font-mono text-xs uppercase tracking-widest text-muted-foreground"
              >
                03 · Extra constraints (optional)
              </label>
              <input
                id="notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={running}
                placeholder="version, naming style, schema prefix, avoid triggers…"
                className="mt-3 w-full rounded-lg border border-input bg-background/60 px-3 py-2.5 font-mono text-[13px] outline-none focus:glow-ring disabled:opacity-60"
              />
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border bg-surface-2 p-1">
              {(["pro", "flash"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={running}
                  onClick={() => setModel(m)}
                  className={cn(
                    "rounded-md px-3 py-2 font-mono text-xs transition-colors",
                    model === m
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m === "pro" ? "deep reasoning" : "fast"}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={generate}
              disabled={running}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-3 font-mono text-sm font-semibold text-primary-foreground transition-transform hover:scale-[1.02] disabled:opacity-60 disabled:hover:scale-100"
            >
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {running ? (phase === "plan" ? "Thinking through it…" : "Writing SQL…") : "Convert to SQL"}
            </button>
            {running && (
              <button
                type="button"
                onClick={stop}
                className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-3 font-mono text-sm text-muted-foreground hover:text-foreground"
              >
                <Square className="h-3.5 w-3.5" /> stop
              </button>
            )}
            <span className="font-mono text-xs text-muted-foreground">
              target: <span className="text-accent">{engineName(engine)}</span>
            </span>
          </div>

          {error && (
            <p className="mt-4 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive-foreground">
              {error}
            </p>
          )}
        </section>

        {(plan || sql) && (
          <section className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
            <div className="panel flex flex-col overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border px-4 py-3">
                <Brain className="h-4 w-4 text-accent" />
                <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
                  reasoning pass
                </span>
                {phase === "plan" && <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />}
              </div>
              <pre
                ref={planRef}
                className="max-h-[32rem] overflow-auto whitespace-pre-wrap p-4 font-mono text-[12px] leading-relaxed text-muted-foreground"
              >
                {plan || "…"}
              </pre>
            </div>

            <div className="panel flex flex-col overflow-hidden">
              <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
                <Terminal className="h-4 w-4 text-primary" />
                <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
                  {engineName(engine)} script
                </span>
                {phase === "sql" && <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />}
                <span className="font-mono text-[11px] text-muted-foreground">{lines} lines</span>
                <div className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={copy}
                    disabled={!cleanSql}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 font-mono text-[11px] hover:border-primary/50 disabled:opacity-40"
                  >
                    <Copy className="h-3.5 w-3.5" /> copy
                  </button>
                  <button
                    type="button"
                    onClick={download}
                    disabled={!cleanSql}
                    className="inline-flex items-center gap-1.5 rounded-md bg-primary/15 px-2.5 py-1.5 font-mono text-[11px] text-primary hover:bg-primary/25 disabled:opacity-40"
                  >
                    <Download className="h-3.5 w-3.5" /> .txt
                  </button>
                </div>
              </div>
              <pre
                ref={sqlRef}
                className="max-h-[32rem] overflow-auto whitespace-pre p-4 font-mono text-[12px] leading-relaxed"
              >
                {cleanSql || "-- waiting for the plan to finish…"}
              </pre>
            </div>
          </section>
        )}

        <footer className="mt-12 font-mono text-[11px] text-muted-foreground">
          Long requests can take a few minutes — the script streams in as it is written.
        </footer>
      </div>
    </main>
  );
}
