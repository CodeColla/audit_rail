import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import {
  LayoutGrid, ClipboardCheck, Diamond, FileText, FolderCheck,
  CalendarClock, BarChart3, Settings, Users, Boxes, ShieldCheck,
  Flame, Database, Truck, Siren, KeyRound, LogOut,
} from "lucide-react";
import { Avatar, OrgLogo } from "./Avatar";
import { GlobalSearch } from "./GlobalSearch";
import { useAuth, useCan } from "../lib/auth";
import { cn } from "../lib/ui";
import { PoweredBy, Wordmark } from "./Brand";

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
        {/* P6: a real uploaded mark when the organisation has one, its initials otherwise.
            The stray `|| "KI"` this replaced hardcoded the FIRST customer's initials as the
            fallback for every organisation in the product. */}
        <OrgLogo name={name} size="sm" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-label font-semibold leading-tight">{name}</span>
          <span className="block text-micro text-txt3">
            {orgs.length > 1 ? `${orgs.length} organisations · switch` : "Compliance workspace"}
          </span>
        </span>
        {orgs.length > 1 && <span className="shrink-0 text-micro text-txt3">▾</span>}
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 overflow-hidden rounded-md border border-bd bg-paper shadow-drawer">
          {orgs.map((o) => (
            <button key={o.tenant_id} onClick={() => pick(o.tenant_id)}
              className={cn("flex w-full items-center justify-between px-3 py-2 text-left text-label hover:bg-canvas",
                o.tenant_id === user?.tenant_id && "font-semibold text-ink")}>
              <span className="truncate">{o.name}</span>
              <span className="ml-2 shrink-0 text-micro capitalize text-txt3">{o.role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The account menu (P6).
 *
 * Replaces a bare sign-out button wedged into the bottom of the sidebar. Two reasons that was
 * wrong for a product: signing out was the ONLY thing you could do with your own account from
 * anywhere in the app — Change password existed as a route with no link to it — and the
 * bottom-left corner is where nobody looks for their profile.
 */
function AccountMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <div ref={box} className="relative shrink-0">
      <button onClick={() => setOpen((o) => !o)} aria-label="Account"
        aria-expanded={open} aria-haspopup="menu"
        className="flex items-center gap-2 rounded-full border border-bd bg-paper py-1 pl-1 pr-2.5 hover:border-hair">
        <Avatar name={user?.full_name} size="sm" />
        <span className="hidden text-label font-medium text-ink sm:block">{user?.full_name}</span>
      </button>
      {open && (
        <div role="menu"
          className="absolute right-0 top-full z-40 mt-1.5 w-60 overflow-hidden rounded-xl border border-bd bg-paper shadow-drawer">
          <div className="flex items-center gap-2.5 border-b border-bd px-3 py-3">
            <Avatar name={user?.full_name} size="md" />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{user?.full_name}</div>
              <div className="truncate text-caption capitalize text-txt3">{user?.role}</div>
            </div>
          </div>
          <Link to="/account/password" onClick={() => setOpen(false)} role="menuitem"
            className="flex items-center gap-2.5 px-3 py-2.5 text-sm text-txt2 hover:bg-canvas hover:text-ink">
            <KeyRound size={15} strokeWidth={1.8} /> Change password
          </Link>
          <button onClick={logout} role="menuitem"
            className="flex w-full items-center gap-2.5 border-t border-bd px-3 py-2.5 text-left text-sm text-txt2 hover:bg-canvas hover:text-bad">
            <LogOut size={15} strokeWidth={1.8} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const can = useCan();
  const loc = useLocation();
  const title = titleFor(loc.pathname);
  const orgName = user?.organisations?.find((o) => o.tenant_id === user?.tenant_id)?.name
    ?? "Your organisation";

  return (
    <div className="grid min-h-screen grid-cols-[236px_1fr]">
      {/* sidebar (fully light) */}
      {/* The nav list scrolls INSIDE the sidebar. Splitting Registers into five menus pushed
          the item count to fourteen; with a plain h-screen column the tail of the list (and
          the pinned Roles/Admin footer) fell below the fold with nothing to scroll — they
          rendered but could not be clicked. */}
      <aside className="sticky top-0 flex h-screen flex-col gap-1 overflow-hidden border-r border-bd bg-paper p-4">
        <div className="flex shrink-0 items-center px-2 pb-4 pt-1.5">
          {/* 16px, not the 20px the old "SR" used: the org switcher sits directly beneath,
              and in-app the identity that matters is the customer's, not ours. */}
          <Wordmark size="sm" />
        </div>
        <div className="shrink-0"><OrgSwitcher /></div>
        <nav className="-mr-2 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pr-2">
          {NAV.map((group) => {
            const items = group.items.filter((n) => can(n.module, "view"));
            if (!items.length) return null;
            return (
              <div key={group.section || "top"} className="flex flex-col gap-0.5">
                {group.section && (
                  <div className="px-3 pb-1 pt-3 text-micro font-semibold uppercase tracking-[0.18em] text-txt3">
                    {group.section}
                  </div>
                )}
                {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cn(
                "relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
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
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
              isActive ? "bg-[rgba(249,115,22,0.09)] text-ink" : "text-txt2 hover:bg-canvas")}>
              <ShieldCheck size={16} strokeWidth={1.7} /> Roles
            </NavLink>
          )}
          {can("users", "view") && (
            <NavLink to="/admin" className={({ isActive }) => cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
              isActive ? "bg-[rgba(249,115,22,0.09)] text-ink" : "text-txt2 hover:bg-canvas")}>
              <Settings size={16} strokeWidth={1.7} /> Admin
            </NavLink>
          )}
          {/* Unconditional, and that fixes a latent bug: both links above are permission
              gated, so a user with neither `roles.view` nor `users.view` used to get a bare
              hairline anchored to the bottom of the sidebar with nothing under it. */}
          <PoweredBy className="px-3 pb-0.5 pt-2.5" />
        </div>
      </aside>

      {/* main */}
      <div className="flex min-w-0 flex-col">
        {/* P6. What this replaced: a grey mono crumb reading "ORG / Page" with the page name
            repeated directly beneath it in 20px — and then a THIRD time by the page's own
            PageHead. "Controls" appeared three times above the fold. The header now carries
            identity (whose workspace am I in, who am I signed in as); the page owns its title. */}
        <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-bd bg-canvas/90 px-6 py-2.5 backdrop-blur">
          <OrgLogo name={orgName} size="md" />
          <div className="min-w-0">
            <div className="truncate text-body font-semibold leading-tight">{orgName}</div>
            {/* Still exposed for the specs and for orientation, just no longer shouting. */}
            <div data-testid="page-title" className="truncate text-caption text-txt3">{title}</div>
          </div>
          {/* P5-S6: this was a bare <input> with no value, no handler and no query — chrome
              that looked like a feature, and the first item in Sumit's Phase 5 feedback. */}
          <GlobalSearch />
          <AccountMenu />
        </header>
        <div className="mx-auto w-full max-w-[1220px] px-6 pb-16 pt-6">{children}</div>
      </div>
    </div>
  );
}
