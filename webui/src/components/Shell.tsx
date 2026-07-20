import { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutGrid, ClipboardCheck, Diamond, FileText, FolderCheck,
  CalendarClock, BarChart3, Settings, Search, Users,
} from "lucide-react";
import { useAuth } from "../lib/auth";
import { cn } from "../lib/ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutGrid, end: true },
  { to: "/people", label: "People", icon: Users },
  { to: "/audits", label: "Audits", icon: ClipboardCheck },
  { to: "/controls", label: "Controls", icon: Diamond },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/evidence", label: "Evidence", icon: FolderCheck },
  { to: "/tasks", label: "Tasks", icon: CalendarClock },
  { to: "/reports", label: "Reports", icon: BarChart3 },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard", "/people": "People", "/audits": "Audits", "/controls": "Controls", "/documents": "Documents",
  "/evidence": "Evidence", "/tasks": "Tasks", "/reports": "Reports", "/admin": "Admin",
};

export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const title = TITLES[loc.pathname] ?? (loc.pathname.startsWith("/audits") ? "Audits" : "");
  const initials = (user?.full_name ?? "?").split(" ").map((s) => s[0]).slice(0, 2).join("");

  return (
    <div className="grid min-h-screen grid-cols-[236px_1fr]">
      {/* sidebar (fully light) */}
      <aside className="sticky top-0 flex h-screen flex-col gap-1 border-r border-bd bg-paper p-4">
        <div className="flex items-center gap-2 px-2 pb-4 pt-1.5">
          <span className="text-[22px] font-bold tracking-[-0.04em] text-ink">SR</span>
          <span className="mb-3 h-[9px] w-[9px] rounded-[2px] bg-accent" />
          <span className="ml-0.5 font-mono text-[11px] font-medium text-txt3">audit_rail</span>
        </div>
        <div className="mx-1 mb-4 flex items-center gap-2.5 rounded-md border border-bd bg-canvas px-2.5 py-2.5">
          <span className="grid h-[26px] w-[26px] place-items-center rounded-md bg-ink text-[12px] font-bold text-white">KI</span>
          <div>
            <div className="text-[12.5px] font-semibold leading-tight">KIAM INTL PVT LTD</div>
            <div className="text-[10.5px] text-txt3">Compliance workspace</div>
          </div>
        </div>
        <div className="px-3 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-txt3">Menu</div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map((n) => (
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
        </nav>
        <div className="mt-auto border-t border-bd pt-2.5">
          <NavLink to="/admin" className={({ isActive }) => cn(
            "flex items-center gap-3 rounded-md px-3 py-2 text-[13px] font-medium",
            isActive ? "bg-[rgba(249,115,22,0.09)] text-ink" : "text-txt2 hover:bg-canvas")}>
            <Settings size={16} strokeWidth={1.7} /> Admin
          </NavLink>
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
            <div className="font-mono text-[12px] text-txt3">KIAM INTL / {title}</div>
            <h1 className="text-[20px] font-semibold tracking-[-0.01em]">{title}</h1>
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
