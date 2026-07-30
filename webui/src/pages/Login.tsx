import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("sumit.t@iesglabs.com");
  const [password, setPassword] = useState("audit_rail");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      // If the account belongs to several organisations the server picks the first
      // deterministically; the sidebar switcher moves between them afterwards.
      await login(email, password);
      nav("/");
    } catch {
      setErr("Invalid email or password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span className="text-[28px] font-bold tracking-[-0.04em] text-ink">SR</span>
          <span className="mb-3.5 h-[11px] w-[11px] rounded-[2px] bg-accent" />
          <span className="ml-1 font-mono text-[13px] text-txt3">audit_rail</span>
        </div>
        <div className="card p-6">
          <div className="eyebrow">Compliance & audit portal</div>
          <h1 className="mb-4 mt-1 text-[20px] font-semibold">Sign in</h1>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="text-[13px] font-medium">
              Email
              <input value={email} onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent" />
            </label>
            <label className="text-[13px] font-medium">
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent" />
            </label>
            {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
            <button disabled={busy} className="btn btn-primary mt-1 justify-center">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
        <p className="mt-3 text-center text-[12px] text-txt3">
          New here? <Link to="/signup" className="underline">Create an organisation</Link>
        </p>
        <p className="mt-1 text-center text-[11.5px] text-txt3">Dev login is prefilled · password “audit_rail”.</p>
      </div>
    </div>
  );
}
