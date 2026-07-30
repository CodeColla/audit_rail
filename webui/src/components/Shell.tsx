import { ReactNode, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutGrid, ClipboardCheck, Diamond, FileText, FolderCheck,
  CalendarClock, BarChart3, Settings, Search, Users, Boxes, ShieldCheck,
  Flame, Database, Truck, Siren,
} from "lucide-react";
import { useAuth, useCan } from "../lib/auth";
import { cn } from "../lib/ui";

// `module` is the permission that makes an entry visible (module.view).
// P4-S3: grouped, because the five registers became separate menus and a flat list of
// fourteen items is unreadable. Obligations is deliberately absent — hidden, not deleted.
const NAV: { section: string; items: {
  to: string; label: string; icon: any; module: string; end?: boolean }[] }[] = [
  { section: "", items: [
    { to: "/", label: "Dashboard", icon: LayoutGrid, end: true, module: "dashboard" },
  ]},
  { section: "Compliance", items: [
    { to: "/audits", label: "Audits", icon: ClipboardCheck, module: "audits" },
    { to: "/controls", label: "Controls", icon: Diamond, module: "controls" },
    { to: "/documents", label: "Documents", icon: FileText, module: "documents" },
    { to: "/evidence", label: "Evidence", icon: FolderCheck, module: "evidence" },
    { to: "/tasks", label: "Tasks", icon: CalendarClock, module: "tasks" },
  ]},
  { section: "Registers", items: [
    { to: "/risks", label: "Risks", icon: Flame, module: "risks" },
    { to: "/assets", label: "Assets", icon: Boxes, module: "assets" },
    { to: "/data", label: "Data inventory", icon: Database, module: "data" },
    { to: "/third-parties", label: "Third parties", icon: Truck, module: "third_parties" },
    { to: "/incidents", label: "Incidents", icon: Siren, module: "incidents" },
  ]},
  { section: "Organisation", items: [
    { to: "/people", label: "People", icon: Users, module: "people" },
    { to: "/reports", label: "Reports", icon: BarChart3, module: "reports" },
  ]},
];

const TITLES: Record<string, string> = {
  "/": "Dashboard", "/people": "People", "/audits": "Audits", "/controls": "Controls",
  "/risks": "Risks", "/assets": "Assets", "/data": "Data inventory",
  "/third-parties": "Third parties", "/incidents": "Incidents",
  "/documents": "Documents", "/roles": "Roles", "/account/password": "Account",
  "/evidence": "Evidence", "/tasks": "Tasks", "/reports": "Reports", "/admin": "Admin",
};

/** Longest matching prefix, so `/risks/view/:id` still reads "Risks" instead of blank. */
function titleFor(pathname: string): string {
  if (TITLES[pathname]) return TITLES[pathname];
  const hit = Object.keys(TITLES)
    .filter((k) => k !== "/" && pathname.startsWith(k))
    .sort((a, b) => b.length - a.length)[0];
  return hit ? TITLES[hit] : "";
}

/**
 * The organisation this session is scoped to, and — for anyone who belongs to more than one
 * — a way to move between them. Switching re-issues the token for the chosen org, which is
 * all that's needed: every query in the app already scopes by the token's tenant.
 */
function OrgSwitcher() {
  const { user, switchOrg } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const orgs = user?.organisations ?? [];
  const current = orgs.find((o) => o.tenant_id === user?.tenant_id);
  const name = current?.name ?? "Your organisation";
  const initials = name.split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  async function pick(tenantId: string) {
    if (tenantId === user?.tenant_id) return setOpen(false);
    setBusy(true);
    try {
      await switchOrg(tenantId);
      window.location.assign("/");     // hard reload so every cached query refetches
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative mx-1 mb-4">
      <button
        onClick={() => orgs.length > 1 && setOpen((o) => !o)}
        disabled={busy}
        aria-label="Organisation"
        className={cn("flex w-full items-center gap-2.5 rounded-md border border-bd bg-canvas px-2.5 py-2.5 text-left",
          orgs.length > 1 && "hover:border-accent")}>
        <span className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-md bg-ink text-[12px] font-bold text-white">
          {initials || "KI"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] font-semibold leading-tight">{name}</span>
          <span className="block text-[10.5px] text-txt3">
            {orgs.length > 1 ? `${orgs.length} organisations · switch` : "Compliance workspace"}
          </span>
        </span>
        {orgs.length > 1 && <span className="shrink-0 text-[10px] text-txt3">▾</span>}
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 overflow-hidden rounded-md border border-bd bg-paper shadow-drawer">
          {orgs.map((o) => (
            <button key={o.tenant_id} onClick={() => pick(o.tenant_id)}
              className={cn("flex w-full items-center justify-between px-3 py-2 text-left text-[12.5px] hover:bg-canvas",
                o.tenant_id === user?.tenant_id && "font-semibold text-ink")}>
              <span className="truncate">{o.name}</span>
              <span className="ml-2 shrink-0 text-[10.5px] capitalize text-txt3">{o.role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const can = useCan();
  const loc = useLocation();
  const title = titleFor(loc.pathname);
  const orgName = user?.organisations?.find((o) => o.tenant_id === user?.tenant_id)?.name
    ?? "Your organisation";
  const initials = (user?.full_name ?? "?").split(" ").map((s) => s[0]).slice(0, 2).join("");

  return (
    <div className="grid min-h-screen grid-cols-[236px_1fr]">
      {/* sidebar (fully light) */}
      {/* The nav list scrolls INSIDE the sidebar. Splitting Registers into five menus pushed
          the item count to fourteen; with a plain h-screen column the tail of the list (and
          the pinned Roles/Admin footer) fell below the fold with nothing to scroll — they
          rendered but could not be clicked. */}
      <aside className="sticky top-0 flex h-screen flex-col gap-1 overflow-hidden border-r border-bd bg-paper p-4">
        <div className="flex shrink-0 items-center gap-2 px-2 pb-4 pt-1.5">
          <span className="text-[22px] font-bold tracking-[-0.04em] text-ink">SR</span>
          <span className="mb-3 h-[9px] w-[9px] rounded-[2px] bg-accent" />
          <span className="ml-0.5 font-mono text-[11px] font-medium text-txt3">audit_rail</span>
        </div>
        <div className="shrink-0"><OrgSwitcher /></div>
        <nav className="-mr-2 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pr-2">
          {NAV.map((group) => {
            const items = group.items.filter((n) => can(n.module, "view"));
            if (!items.length) return null;
            return (
              <div key={group.section || "top"} className="flex flex-col gap-0.5">
                {group.section && (
                  <div className="px-3 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-txt3">
                    {group.section}
                  </div>
                )}
                {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cn(
                "relative flex items-center gap-3 rounded-md px-3 py-2 text-[13.5px] font-medium",
                isActive ? "bg-[rgba(249,115,22,0.09)] font-semibold text-ink" : "text-txt2 hover:bg-canvas hover:text-ink")}>
              {({ isActive }) => (
                <>
                  {isActive && <span className="absolute -left-4 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-accent" />}
                  <n.icon size={17} strokeWidth={1.7} className={isActive ? "text-accent" : "opacity-70"} />
                  {n.label}
                </>
              )}
            </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="shrink-0 border-t border-bd pt-2.5">
          {can("roles", "view") && (
            <NavLink to="/roles" className={({ isActive }) => cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-[13px] font-medium",
              isActive ? "bg-[rgba(249,115,22,0.09)] text-ink" : "text-txt2 hover:bg-canvas")}>
              <ShieldCheck size={16} strokeWidth={1.7} /> Roles
            </NavLink>
          )}
          {can("users", "view") && (
            <NavLink to="/admin" className={({ isActive }) => cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-[13px] font-medium",
              isActive ? "bg-[rgba(249,115,22,0.09)] text-ink" : "text-txt2 hover:bg-canvas")}>
              <Settings size={16} strokeWidth={1.7} /> Admin
            </NavLink>
          )}
          <button onClick={logout} className="flex w-full items-center gap-2.5 px-2 py-2 text-left">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-ink text-[11px] font-semibold text-white">{initials}</span>
            <span>
              <span className="block text-[12.5px] font-medium text-ink">{user?.full_name}</span>
              <span className="block text-[10.5px] text-txt3 capitalize">{user?.role} · sign out</span>
            </span>
          </button>
        </div>
      </aside>

      {/* main */}
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-bd bg-canvas/90 px-6 py-3 backdrop-blur">
          <div>
            {/* the org name used to be hardcoded "KIAM INTL", which was wrong the moment
                P4-S1 let an account belong to more than one organisation */}
            <div className="font-mono text-[12px] text-txt3">{orgName} / {title}</div>
            {/* chrome, not the page heading — each page renders its own <h1> via PageHead */}
            <div data-testid="page-title" className="text-[20px] font-semibold tracking-[-0.01em]">{title}</div>
          </div>
          <div className="ml-auto flex items-center gap-2 rounded-full border border-bd bg-paper px-3 py-1.5 text-txt3">
            <Search size={15} />
            <input placeholder="Search…" className="w-40 bg-transparent text-[13px] text-ink outline-none placeholder:text-txt3" />
          </div>
        </header>
        <div className="mx-auto w-full max-w-[1220px] px-6 pb-16 pt-6">{children}</div>
      </div>
    </div>
  );
}
