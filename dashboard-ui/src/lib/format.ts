export const fmtNumber = (n: unknown): string =>
  typeof n === "number" && Number.isFinite(n) ? String(Number.isInteger(n) ? n : Number(n.toPrecision(6))) : String(n);

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtNumber(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return `[${v.map(fmtValue).join(", ")}]`;
  return JSON.stringify(v);
}

export function fmtLimit(limit: { min?: number | null; max?: number | null; equals?: number | null } | null | undefined): string {
  if (!limit) return "—";
  const parts: string[] = [];
  if (limit.min != null) parts.push(`≥ ${fmtNumber(limit.min)}`);
  if (limit.max != null) parts.push(`≤ ${fmtNumber(limit.max)}`);
  if (limit.equals != null) parts.push(`= ${fmtNumber(limit.equals)}`);
  return parts.length ? parts.join(", ") : "—";
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} min ${String(whole % 60).padStart(2, "0")} s`;
}

/** Flatten nested plain objects to dotted keys; arrays and scalars stay as leaves. */
export function flatten(obj: Record<string, unknown> | undefined, prefix = "", out: [string, unknown][] = []) {
  for (const [key, value] of Object.entries(obj ?? {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
      flatten(value as Record<string, unknown>, path, out);
    } else out.push([path, value]);
  }
  return out;
}
