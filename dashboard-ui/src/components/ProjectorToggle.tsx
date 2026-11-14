import { Presentation } from "lucide-react";
import { useTheme } from "@/lib/theme";

/** High-contrast light theme with larger type, for a projector or a shared screen. */
export function ProjectorToggle() {
  const { projector, toggleProjector } = useTheme();
  return (
    <button type="button" onClick={toggleProjector} aria-pressed={projector} title="High-contrast light theme with larger type, for a projector"
      className="flex items-center gap-1.5 rounded-md border px-3 py-1 text-xs hover:bg-muted aria-pressed:border-primary aria-pressed:bg-accent">
      <Presentation className="h-3.5 w-3.5" /> Projector theme
    </button>
  );
}
