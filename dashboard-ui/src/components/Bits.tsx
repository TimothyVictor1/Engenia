import type { ReactNode } from "react";
import { flatten, fmtFact } from "@/lib/format";

export function KV({ data, empty = "Nothing reported." }: { data: Record<string, unknown> | undefined; empty?: string }) {
  const rows = flatten(data);
  if (!rows.length) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b last:border-0">
            <th scope="row" className="py-1.5 pr-4 text-left align-top font-mono text-xs font-normal text-muted-foreground">{k.replace(/([_.])/g, "$1\u200b")}</th>
            <td className="py-1.5 text-right align-top font-mono text-xs tabular-nums">{fmtFact(k, v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`rounded-lg border bg-card p-4 ${className}`}>{children}</div>
);

export const Section = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="mt-8 first:mt-0">
    <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</h2>
    {children}
  </section>
);
