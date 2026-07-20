import { createContext, useContext, useState, ReactNode } from "react";
import { api } from "./api";

type User = {
  kind: "member" | "guest";
  role: string;
  tenant_id: string | null;
  full_name: string;
  assessment_id?: string | null;
} | null;

const AuthCtx = createContext<{
  user: User;
  login: (email: string, password: string) => Promise<void>;
  enterAsGuest: (token: string) => Promise<void>;
  logout: () => void;
}>({ user: null, login: async () => {}, enterAsGuest: async () => {}, logout: () => {} });

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

  async function login(email: string, password: string) {
    const { data } = await api.post("/auth/login", { email, password });
    localStorage.setItem("ar_token", data.access_token);
    persist({ kind: "member", role: data.role, tenant_id: data.tenant_id, full_name: data.full_name });
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

  return <AuthCtx.Provider value={{ user, login, enterAsGuest, logout }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
