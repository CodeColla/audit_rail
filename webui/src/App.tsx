import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Shell } from "./components/Shell";
import { AuditorApp } from "./components/AuditorApp";
import Login from "./pages/auth/Login";
import Signup from "./pages/auth/Signup";
import ChangePassword from "./pages/auth/ChangePassword";
import AuditorEntry from "./pages/audits/AuditorEntry";
import Sign from "./pages/auth/Sign";
import Dashboard from "./pages/admin/Dashboard";
import Controls from "./pages/controls/Controls";
import Frameworks from "./pages/controls/Frameworks";
import Mappings from "./pages/documents/Mappings";
import ControlDetail from "./pages/controls/ControlDetail";
import Registers from "./pages/registers/Registers";
import Risks from "./pages/registers/Risks";
import Assets from "./pages/registers/Assets";
import DataInventory from "./pages/registers/DataInventory";
import ThirdParties from "./pages/registers/ThirdParties";
import Incidents from "./pages/registers/Incidents";
import Roles from "./pages/people/Roles";
import People from "./pages/people/People";
import Documents from "./pages/documents/Documents";
import DocumentDetail from "./pages/documents/DocumentDetail";
import Audits from "./pages/audits/Audits";
import Import from "./pages/documents/Import";
import Workspace from "./pages/audits/Workspace";
import Evidence from "./pages/admin/Evidence";
import EvidenceDetail from "./pages/admin/EvidenceDetail";
import Tasks from "./pages/admin/Tasks";
import Reports from "./pages/admin/Reports";
import Admin from "./pages/admin/Admin";

export default function App() {
  const { user } = useAuth();
  // The magic-link signing page is public and standalone — reachable in every auth state,
  // so it's matched before the guest/member/login branches (a logged-out signer must not
  // bounce to /login). It's always a fresh full-page load from the emailed link.
  if (window.location.pathname.startsWith("/sign/"))
    return (
      <Routes>
        <Route path="/sign/:token" element={<Sign />} />
      </Routes>
    );
  // The auditor invite link must work in ANY auth state. It used to be routed only in the
  // logged-out branch, so a member who generated the link and clicked it landed on the
  // wildcard and was silently redirected home — making the link look broken to the very
  // person sharing it. AuditorEntry swaps the session for the guest one.
  if (window.location.pathname.startsWith("/auditor"))
    return (
      <Routes>
        <Route path="/auditor" element={<AuditorEntry />} />
      </Routes>
    );
  if (user?.kind === "guest") return <AuditorApp />;
  if (!user) {
    return (
      <Routes>
        <Route path="/auditor" element={<AuditorEntry />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  // The 30-day policy has elapsed: nothing else is reachable until it's changed. The API
  // enforces this independently — this only saves the user from 400s on every screen.
  if (user.must_change_password) {
    return (
      <Routes>
        <Route path="*" element={<ChangePassword />} />
      </Routes>
    );
  }
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/audits" element={<Audits />} />
        <Route path="/audits/import" element={<Import />} />
        <Route path="/mappings" element={<Mappings />} />
        <Route path="/mappings/:id" element={<Mappings />} />
        <Route path="/audits/:id" element={<Workspace />} />
        <Route path="/people" element={<People />} />
        <Route path="/controls" element={<Controls />} />
        <Route path="/frameworks" element={<Frameworks />} />
        <Route path="/frameworks/:id" element={<Frameworks />} />
        {/* P4-S3: the five registers are top-level modules with deep-linkable rows.
            /registers redirects to /risks so old links still work. */}
        <Route path="/registers" element={<Registers />} />
        <Route path="/risks" element={<Risks />} />
        <Route path="/risks/view/:id" element={<Risks />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/view/:id" element={<Assets />} />
        <Route path="/data" element={<DataInventory />} />
        <Route path="/third-parties" element={<ThirdParties />} />
        <Route path="/third-parties/view/:id" element={<ThirdParties />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/incidents/view/:id" element={<Incidents />} />
        {/* P4-S5: a control gets a full page, not a Drawer — it's the record the whole
            "answer once, reuse everywhere" premise rests on, too much to cram into 560px. */}
        <Route path="/controls/view/:id" element={<ControlDetail />} />
        {/* P4-S8: a real page, not the list with a drawer on top. The old form resolved
            the drawer out of the LIST response, so a deep link only worked when the row
            happened to be in the fetch — otherwise you got the vault and no explanation. */}
        <Route path="/evidence/view/:id" element={<EvidenceDetail />} />
        <Route path="/people/view/:id" element={<People />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/documents/:id" element={<DocumentDetail />} />
        <Route path="/policies" element={<Navigate to="/documents?type=POLICY" replace />} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/roles" element={<Roles />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/account/password" element={<ChangePassword />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="/signup" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
