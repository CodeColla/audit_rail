import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { AuthLayout } from "../../components/AuthLayout";
import { inputCls } from "../../lib/ui";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  // Empty, deliberately. These two fields used to be prefilled with a real admin address and
  // the literal password, which line 59 then also printed on the page — fine on one laptop,
  // and an admin account handed to anyone who loads the page on a shared testing server.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
    <AuthLayout variant="split">
      <div className="card p-6">
        <div className="eyebrow">Compliance &amp; audit portal</div>
        <h1 className="mb-4 mt-1 text-title font-semibold">Sign in</h1>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <div>
            <label htmlFor="login-email" className="text-sm font-medium">Email</label>
            <input id="login-email" type="email" required autoFocus autoComplete="username"
              value={email} onChange={(e) => setEmail(e.target.value)}
              className={inputCls + " mt-1"} />
          </div>
          <div>
            <label htmlFor="login-password" className="text-sm font-medium">Password</label>
            <input id="login-password" type="password" required autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)}
              className={inputCls + " mt-1"} />
          </div>
          {err && <div role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
          <button disabled={busy} className="btn btn-primary mt-1 justify-center">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
      <p className="mt-3 text-center text-label text-txt2">
        New here? <Link to="/signup" className="underline">Create an organisation</Link>
      </p>
    </AuthLayout>
  );
}
