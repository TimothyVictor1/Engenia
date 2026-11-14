import type { ReactNode } from "react";

/** A one-line "why this matters" tooltip, on hover and on keyboard focus. */
export function Tip({ text, children, align = "center" }: { text: string; children: ReactNode; align?: "center" | "left" | "right" }) {
  const pos = align === "left" ? "left-0" : align === "right" ? "right-0" : "left-1/2 -translate-x-1/2";
  return (
    <span className="group/tip relative inline-flex" tabIndex={0}>
      {children}
      <span role="tooltip" className={`pointer-events-none absolute top-full z-50 mt-1.5 w-60 rounded-md border bg-card p-2 text-left text-xs font-normal normal-case leading-snug tracking-normal text-foreground opacity-0 shadow-lg transition-opacity group-hover/tip:opacity-100 group-focus/tip:opacity-100 ${pos}`}>
        {text}
      </span>
    </span>
  );
}
