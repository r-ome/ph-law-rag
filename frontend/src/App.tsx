import { Link, Route, Routes } from "react-router-dom";
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

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-6 border-b px-6 py-3">
        <Link to="/" className="text-lg font-semibold">
          PH Law RAG — Workbench
        </Link>
        <nav className="flex gap-4 text-sm">
          <Link to="/">Corpus</Link>
          <Link to="/chat">Chat</Link>
          <Link to="/dashboard">Dashboard</Link>
          <Link to="/ingestion">Ingestion</Link>
          <Link to="/lab">Lab</Link>
          <Link to="/evals">Evals</Link>
          <Link to="/observability">Observability</Link>
          <Link to="/logs">Logs</Link>
          <Link to="/health">Health</Link>
        </nav>
      </header>
      <main className="p-6">
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
  );
}
