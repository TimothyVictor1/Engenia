import type { Result, StageStatus } from "@/lib/types";

type Any = StageStatus | Result | "unverified" | "open" | "accepted" | "rejected";

const TONE: Record<string, { badge: string; bar: string }> = {
  done: { badge: "bg-success/10 text-success", bar: "bg-success" },
  pass: { badge: "bg-success/10 text-success", bar: "bg-success" },
  accepted: { badge: "bg-success/10 text-success", bar: "bg-success" },
  cached: { badge: "bg-success/10 text-success", bar: "bg-success/50" },
  running: { badge: "bg-info/10 text-info", bar: "bg-info animate-pulse" },
  open: { badge: "bg-info/10 text-info", bar: "bg-info" },
  failed: { badge: "bg-destructive/10 text-destructive", bar: "bg-destructive" },
  fail: { badge: "bg-destructive/10 text-destructive", bar: "bg-destructive" },
  error: { badge: "bg-warning/20 text-foreground", bar: "bg-warning" },
  blocked: { badge: "bg-muted text-muted-foreground", bar: "bg-muted-foreground/40" },
};
const IDLE = { badge: "bg-muted text-muted-foreground", bar: "bg-border" };

export const tone = (status: string) => TONE[status] ?? IDLE;

export function StatusBadge({ status, count }: { status: Any | string; count?: number }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11px] font-medium ${tone(status).badge}`}>
      {status.replace(/_/g, " ")}
      {count !== undefined && <b>{count}</b>}
    </span>
  );
}

export function EvidenceTag({ level }: { level: string | null | undefined }) {
  const colour = level === "measured" ? "text-success border-success/40"
    : level === "simulated" ? "text-info border-info/40"
    : level === "calculated" ? "text-primary border-primary/40" : "text-muted-foreground";
  return <span className={`ml-2 rounded-full border px-1.5 font-mono text-[10px] ${colour}`}>{level ?? "no level"}</span>;
}
