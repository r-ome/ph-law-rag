import { useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import {
  Activity,
  BarChart3,
  BookOpen,
  Database,
  FlaskConical,
  HeartPulse,
  MessageSquareText,
  Moon,
  Search,
  ScrollText,
  Sun,
} from "lucide-react";
import CorpusList from "@/routes/CorpusList";
import CorpusDetail from "@/routes/CorpusDetail";
import Chat from "@/routes/Chat";
import Dashboard from "@/routes/Dashboard";
import Ingestion from "@/routes/Ingestion";
import Health from "@/routes/Health";
import Lab from "@/routes/Lab";
import Observability from "@/routes/Observability";
import Logs from "@/routes/Logs";
import Evals from "@/routes/Evals";
import EvalDetail from "@/routes/EvalDetail";

const navGroups = [
  {
    label: "Research",
    items: [
      { to: "/chat", label: "Ask", icon: MessageSquareText },
      { to: "/", label: "Corpus", icon: BookOpen, end: true },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: BarChart3 },
      { to: "/ingestion", label: "Ingestion", icon: Database },
    ],
  },
  {
    label: "Quality",
    items: [
      { to: "/evals", label: "Evaluations", icon: Activity },
      { to: "/lab", label: "Retrieval Lab", icon: FlaskConical },
      { to: "/observability", label: "Observability", icon: Activity },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/logs", label: "Logs", icon: ScrollText },
      { to: "/health", label: "Health", icon: HeartPulse },
    ],
  },
];

function pageLabel(pathname: string) {
  if (pathname.startsWith("/documents/")) return "Document";
  if (pathname.startsWith("/chat")) return "Ask";
  if (pathname.startsWith("/dashboard")) return "Dashboard";
  if (pathname.startsWith("/ingestion")) return "Ingestion";
  if (pathname.startsWith("/lab")) return "Retrieval Lab";
  if (pathname.startsWith("/evals/")) return "Evaluation Detail";
  if (pathname.startsWith("/evals")) return "Evaluations";
  if (pathname.startsWith("/observability")) return "Observability";
  if (pathname.startsWith("/logs")) return "Logs";
  if (pathname.startsWith("/health")) return "Health";
  return "Corpus";
}

function WorkbenchShell() {
  const location = useLocation();
  const [dark, setDark] = useState(false);
  const crumb = pageLabel(location.pathname);

  return (
    <div className={`workbench ${dark ? "dark" : ""}`}>
      <aside className="workbench-sidebar">
        <Link to="/" className="workbench-brand" aria-label="PH Law Research Console home">
          <span className="brand-mark">§</span>
          <span>
            <span className="brand-name">PH Law</span>
            <span className="brand-subtitle">Research Console</span>
          </span>
        </Link>

        <div className="sidebar-search" aria-label="Corpus search shortcut">
          <Search aria-hidden="true" />
          <span>Search corpus…</span>
          <kbd>⌘K</kbd>
        </div>

        <nav className="workbench-nav" aria-label="Workbench navigation">
          {navGroups.map((group) => (
            <section key={group.label} className="nav-group">
              <h2>{group.label}</h2>
              <div>
                {group.items.map(({ to, label, icon: Icon, end }) => (
                  <NavLink key={to} to={to} {...(end ? { end: true } : {})} className="nav-link">
                    <Icon aria-hidden="true" />
                    <span>{label}</span>
                    {label === "Health" && <span className="health-dot" aria-label="System available" />}
                  </NavLink>
                ))}
              </div>
            </section>
          ))}
        </nav>

        <div className="sidebar-session">
          <span className="session-avatar">LS</span>
          <span>
            <strong>Local Session</strong>
            <small>Local-first research</small>
          </span>
        </div>
      </aside>

      <div className="workbench-main">
        <header className="workbench-topbar">
          <div className="breadcrumbs"><span>Workbench</span><span>/</span><strong>{crumb}</strong></div>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setDark((value) => !value)}
            aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
          >
            {dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            <span>{dark ? "Light" : "Dark"}</span>
          </button>
        </header>

        <main className="workbench-content">
          <Routes>
            <Route path="/" element={<CorpusList />} />
            <Route path="/documents/:docId" element={<CorpusDetail />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:sessionId" element={<Chat />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/ingestion" element={<Ingestion />} />
            <Route path="/lab" element={<Lab />} />
            <Route path="/evals" element={<Evals />} />
            <Route path="/evals/:tag" element={<EvalDetail />} />
            <Route path="/observability" element={<Observability />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/health" element={<Health />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return <WorkbenchShell />;
}
