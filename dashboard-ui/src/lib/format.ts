/** Integers as they are; other numbers to 4 significant figures (191.872 -> 191.9, 0.010204 -> 0.0102). */
export const fmtNumber = (n: unknown): string =>
  typeof n === "number" && Number.isFinite(n) ? String(Number.isInteger(n) ? n : Number(n.toPrecision(4))) : String(n);

// Units carried by a fact's name, longest suffix first. A dotted path is searched from the leaf up
// because `mode_power_mw.sleep` keeps its unit on the parent.
const KEY_UNITS: [string, string][] = [
  ["_mwh_per_day", "mWh/day"], ["_mm_h", "mm/h"], ["_w_m2", "W/m²"], ["_g_rms", "g rms"], ["_hours", "h"], ["_mwh", "mWh"], ["_wh", "Wh"], ["_mah", "mAh"], ["_mm", "mm"], ["_cm3", "cm³"],
  ["_kg_m3", "kg/m³"], ["_speed_ms", "m/s"], ["_ms", "ms"], ["_kb", "kB"], ["_mw", "mW"], ["_days", "days"],
  ["_percent", "%"], ["_g", "g"], ["_c", "°C"], ["_m", "m"], ["_s", "s"], ["_hz", "Hz"],
];
export function unitOfKey(key: string): string {
  for (const segment of key.split(".").reverse()) {
    const lower = segment.toLowerCase();
    for (const [suffix, unit] of KEY_UNITS) if (lower.endsWith(suffix)) return unit;
  }
  return "";
}
const numeric = (v: unknown) => typeof v === "number" || (Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "number"));
/** A fact value with its unit taken from the key; non-numbers get no unit. */
export function fmtFact(key: string, value: unknown): string {
  const unit = numeric(value) ? unitOfKey(key) : "";
  const text = Array.isArray(value) && numeric(value) ? value.map(fmtNumber).join(" × ") : fmtValue(value);
  return unit ? `${text} ${unit}` : text;
}

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
