import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Shell } from "./components/Shell";
import { AuditorApp } from "./components/AuditorApp";
import Login from "./pages/Login";
import AuditorEntry from "./pages/AuditorEntry";
import Dashboard from "./pages/Dashboard";
import Controls from "./pages/Controls";
import People from "./pages/People";
import Documents from "./pages/Documents";
import DocumentDetail from "./pages/DocumentDetail";
import Audits from "./pages/Audits";
import Import from "./pages/Import";
import Workspace from "./pages/Workspace";
import Evidence from "./pages/Evidence";
import Tasks from "./pages/Tasks";
import Reports from "./pages/Reports";
import Admin from "./pages/Admin";

export default function App() {
  const { user } = useAuth();
  if (user?.kind === "guest") return <AuditorApp />;
  if (!user) {
    return (
      <Routes>
        <Route path="/auditor" element={<AuditorEntry />} />
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/audits" element={<Audits />} />
        <Route path="/audits/import" element={<Import />} />
        <Route path="/audits/:id" element={<Workspace />} />
        <Route path="/people" element={<People />} />
        <Route path="/controls" element={<Controls />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/documents/:id" element={<DocumentDetail />} />
        <Route path="/policies" element={<Navigate to="/documents?type=POLICY" replace />} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
