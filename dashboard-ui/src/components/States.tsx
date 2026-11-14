import type { ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { Resource } from "@/lib/store";

export function Loading({ text = "Loading…" }: { text?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> {text}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error | null; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-sm">
      <span className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="h-4 w-4" /> Could not load this view
      </span>
      <span className="text-muted-foreground">{error?.message || "Unknown error."}</span>
      {onRetry && <button onClick={onRetry} className="rounded-md border px-3 py-1 text-xs font-medium hover:bg-muted">Try again</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed p-8 text-center text-sm">
      <Inbox className="mb-1 h-5 w-5 text-muted-foreground" />
      <strong>{title}</strong>
      {children && <span className="max-w-md text-muted-foreground">{children}</span>}
    </div>
  );
}

export function StaleNotice({ error }: { error: Error | null }) {
  if (!error) return null;
  return (
    <div role="status" className="mb-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
      Could not refresh: {error.message} Showing the last data received.
    </div>
  );
}

/** Standard handling of a resource: loading, error, or the children once data exists. */
export function Gate<T>({ res, children }: { res: Resource<T>; children: (data: T) => ReactNode }) {
  if (res.status === "loading") return <Loading />;
  if (res.status === "error" || !res.data) return <ErrorState error={res.error} onRetry={res.reload} />;
  return <><StaleNotice error={res.error} />{children(res.data)}</>;
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle: string; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{subtitle}</p>
      </div>
      {right}
    </div>
  );
}
