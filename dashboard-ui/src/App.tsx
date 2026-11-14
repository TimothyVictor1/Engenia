import { Suspense, lazy, type ComponentType } from "react";
import { Box, FileSignature, FlaskConical, FolderOpen, Gauge, ListChecks, Radio, Workflow, type LucideIcon } from "lucide-react";
import brand from "./assets/engenia-mark.png";
import { DashboardProvider, useDash, useResource } from "./lib/store";
import { VIEWS, useRoute, type View } from "./lib/route";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Loading } from "./components/States";
import { Mission } from "./views/Mission";
import { Pipeline } from "./views/Pipeline";
import { usePresentation } from "./lib/present";
import { Requirements } from "./views/Requirements";
import { Contract } from "./views/Contract";
import { Scenarios } from "./views/Scenarios";
import { Files } from "./views/Files";
import { ProjectorToggle } from "./components/ProjectorToggle";

// three.js is large and only two views need it: load it when one of them opens.
const Design = lazy(() => import("./views/Design").then((m) => ({ default: m.Design })));
const Studio = lazy(() => import("./views/Studio").then((m) => ({ default: m.Studio })));

const NAV: Record<View, { title: string; icon: LucideIcon }> = {
  mission: { title: "Mission Control", icon: Gauge },
  pipeline: { title: "Pipeline", icon: Workflow },
  requirements: { title: "Requirements", icon: ListChecks },
  design: { title: "Design", icon: Box },
  contract: { title: "Contract", icon: FileSignature },
  scenarios: { title: "Scenarios", icon: FlaskConical },
  files: { title: "Files", icon: FolderOpen },
  studio: { title: "Live studio", icon: Radio },
};

function Header() {
  const { sample, setSample, connection } = useDash();
  const pipeline = useResource("pipeline", (api) => api.pipeline());
  const dot = connection === "live" ? "bg-success" : connection === "sample" ? "bg-info" : "bg-muted-foreground";
  const label = connection === "live" ? "Live" : connection === "sample" ? "Sample data" : "Offline";
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-card/90 px-4 backdrop-blur">
      <span className="rounded-md bg-muted px-2.5 py-1 font-mono text-xs font-medium">
        {pipeline.data?.project ?? (sample ? "harbour-listener" : "—")}
      </span>
      <div className="ml-auto flex items-center gap-4 text-xs">
        <ProjectorToggle />
        <span className="flex items-center gap-1.5 text-muted-foreground" role="status">
          <span className={`h-2 w-2 rounded-full ${dot}`} /> {label}
        </span>
        <label className="flex cursor-pointer items-center gap-2 font-medium">
          <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={sample === true}
            disabled={sample === null} onChange={(e) => setSample(e.target.checked)} />
          Use sample data
        </label>
      </div>
    </header>
  );
}

function Shell() {
  const { view, param } = useRoute();
  const { sample } = useDash();
  const [present] = usePresentation();
  const Page = ({ mission: Mission, pipeline: Pipeline, requirements: Requirements, design: Design, contract: Contract, scenarios: Scenarios, files: Files, studio: Studio } as Record<View, ComponentType<{ param: string }>>)[view];
  if (present && view === "mission") {
    // Presentation mode: no sidebar or header, only the view.
    return (
      <main id="main" className="min-h-screen w-full p-4 sm:p-6">
        <ErrorBoundary resetKey="present">
          {sample === null ? <Loading text="Connecting…" /> : <Suspense fallback={<Loading />}><Mission /></Suspense>}
        </ErrorBoundary>
      </main>
    );
  }
  return (
    <div className="flex min-h-screen w-full flex-col overflow-x-clip md:flex-row">
      <aside className="shrink-0 border-b bg-surface md:w-56 md:border-b-0 md:border-r">
        <a href="#/pipeline" className="flex h-14 items-center gap-2.5 border-b px-4">
          <img src={brand} alt="" width={28} height={28} className="h-7 w-7" />
          <span className="leading-none">
            <span className="block text-[17px] font-semibold tracking-[-0.03em]">Engenia</span>
            <span className="mt-1 block font-mono text-[8px] uppercase tracking-[0.18em] text-muted-foreground">Engineering, orchestrated</span>
          </span>
        </a>
        <nav aria-label="Views" className="flex gap-1 overflow-x-auto p-2 md:flex-col">
          {VIEWS.map((v) => {
            const Icon = NAV[v].icon;
            return (
              <a key={v} href={`#/${v}`} aria-current={v === view ? "page" : undefined}
                className="flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-foreground">
                <Icon className="h-4 w-4" /> {NAV[v].title}
              </a>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main id="main" className="flex-1 p-4 sm:p-6">
          <ErrorBoundary resetKey={`${view}:${sample}`}>
            {sample === null ? <Loading text="Connecting…" /> : (
              <Suspense fallback={<Loading />}><Page param={param} /></Suspense>
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export function App() {
  return (
    <DashboardProvider>
      <Shell />
    </DashboardProvider>
  );
}
