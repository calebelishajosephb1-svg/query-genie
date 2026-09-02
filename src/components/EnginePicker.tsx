import { ENGINES, type EngineId } from "@/lib/engines";
import { cn } from "@/lib/utils";

export function EnginePicker({
  value,
  onChange,
  disabled,
}: {
  value: EngineId;
  onChange: (id: EngineId) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {ENGINES.map((e) => {
        const active = e.id === value;
        return (
          <button
            key={e.id}
            type="button"
            disabled={disabled}
            onClick={() => onChange(e.id)}
            aria-pressed={active}
            className={cn(
              "rounded-lg border px-3 py-2.5 text-left transition-all disabled:opacity-50",
              active
                ? "border-primary/60 bg-primary/10 glow-ring"
                : "border-border bg-surface-2/60 hover:border-primary/40 hover:bg-surface-2",
            )}
          >
            <div
              className={cn(
                "font-mono text-sm font-semibold",
                active ? "text-primary" : "text-foreground",
              )}
            >
              {e.name}
            </div>
            <div className="mt-0.5 text-[11px] leading-tight text-muted-foreground">{e.blurb}</div>
          </button>
        );
      })}
    </div>
  );
}
