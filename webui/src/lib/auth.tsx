import { createContext, useContext, useState, ReactNode } from "react";
import { api } from "./api";

export type Org = { tenant_id: string; name: string; role: string };

type User = {
  kind: "member" | "guest";
  role: string;
  tenant_id: string | null;
  full_name: string;
  /** Present for members; `/auth/me` has always returned it. Needed to tell "this is my own
   * account" from someone else's — see People's LoginCard, which must not offer you a button
   * that revokes your own access. */
  user_id?: string;
  assessment_id?: string | null;
  // P4: an account can belong to several organisations and switch between them.
  organisations?: Org[];
  /** "module.action" strings resolved server-side — see api/permissions.py. */
  permissions?: string[];
  is_super_admin?: boolean;
  must_change_password?: boolean;
  password_expires_in_days?: number | null;
} | null;

type SignupIn = {
  full_name: string; email: string; password: string;
  organisation_name: string; gst_number?: string;
};

const AuthCtx = createContext<{
  user: User;
  login: (email: string, password: string, tenantId?: string) => Promise<void>;
  signup: (body: SignupIn) => Promise<void>;
  enterAsGuest: (token: string) => Promise<void>;
  acceptInvite: (token: string, password: string) => Promise<void>;
  switchOrg: (tenantId: string) => Promise<void>;
  clearPasswordWarning: () => void;
  logout: () => void;
}>({
  user: null, login: async () => {}, signup: async () => {}, enterAsGuest: async () => {},
  acceptInvite: async () => {}, switchOrg: async () => {}, clearPasswordWarning: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User>(() => {
    // A session needs BOTH the identity and a token. If the token is gone (e.g. it
    // expired), treat it as logged out instead of a ghost session that 401-loops.
    const raw = localStorage.getItem("ar_user");
    const tok = localStorage.getItem("ar_token");
    return raw && tok ? JSON.parse(raw) : null;
  });

  function persist(u: User) {
    localStorage.setItem("ar_user", JSON.stringify(u));
    setUser(u);
  }

  /** Every token-issuing response has the same shape, so unpack it in one place. */
  function adoptToken(data: any) {
    localStorage.setItem("ar_token", data.access_token);
    persist({
      kind: "member", user_id: data.user_id, role: data.role, tenant_id: data.tenant_id,
      full_name: data.full_name, organisations: data.organisations ?? [],
      permissions: data.permissions ?? [], is_super_admin: !!data.is_super_admin,
      must_change_password: !!data.must_change_password,
      password_expires_in_days: data.password_expires_in_days ?? null,
    });
  }

  async function login(email: string, password: string, tenantId?: string) {
    const { data } = await api.post("/auth/login",
      { email, password, ...(tenantId ? { tenant_id: tenantId } : {}) });
    adoptToken(data);
  }

  async function signup(body: SignupIn) {
    const { data } = await api.post("/auth/signup", body);
    adoptToken(data);
  }

  async function acceptInvite(token: string, password: string) {
    const { data } = await api.post("/auth/accept-invite", { token, password });
    adoptToken(data);
  }

  async function switchOrg(tenantId: string) {
    const { data } = await api.post("/auth/switch-org", { tenant_id: tenantId });
    adoptToken(data);
  }

  /** After a successful change we no longer need to force the user through it. */
  function clearPasswordWarning() {
    if (user) persist({ ...user, must_change_password: false, password_expires_in_days: 30 });
  }

  async function enterAsGuest(token: string) {
    localStorage.setItem("ar_token", token);
    const { data } = await api.get("/auth/me"); // validates the token + tells us it's a guest
    persist({
      kind: data.kind, role: data.role, tenant_id: data.tenant_id,
      full_name: data.full_name, assessment_id: data.assessment_id,
    });
  }

  function logout() {
    localStorage.removeItem("ar_token");
    localStorage.removeItem("ar_user");
    setUser(null);
  }

  return (
    <AuthCtx.Provider value={{ user, login, signup, enterAsGuest, acceptInvite,
                               switchOrg, clearPasswordWarning, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);

/**
 * Permission check for the UI. This HIDES things — it is not the security boundary; the API
 * re-checks every request (api/permissions.require). Guests have no module permissions and
 * never render the member shell.
 */
export function useCan() {
  const { user } = useAuth();
  const held = user?.permissions;
  return (module: string, action: string) => !!held?.includes(`${module}.${action}`);
}
