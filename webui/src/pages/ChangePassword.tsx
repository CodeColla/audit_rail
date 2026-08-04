import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errText } from "../lib/api";
import { useAuth } from "../lib/auth";

/**
 * Change password — also the forced-change screen.
 *
 * When `user.must_change_password` is set (the 30-day policy has elapsed), App.tsx routes
 * every path here and there is no way past it until the password is changed. The server is
 * the real gate: it rejects reuse of the last 3 and enforces the strength rules.
 */
export default function ChangePassword() {
  const { user, clearPasswordWarning, logout } = useAuth();
  const nav = useNavigate();
  const forced = !!user?.must_change_password;
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const field = "mt-1 w-full rounded-md border border-bd px-3 py-2 text-body outline-none focus:border-accent";

  const mismatch = !!confirm && next !== confirm;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (mismatch) return;
    setBusy(true); setErr("");
    try {
      await api.post("/auth/change-password", { current_password: cur, new_password: next });
      clearPasswordWarning();
      nav("/");
    } catch (e: any) {
      setErr(errText(e, "Could not change the password."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="card p-6">
          <div className="eyebrow">Account</div>
          <h1 className="mb-1 mt-1 text-title font-semibold">
            {forced ? "Your password has expired" : "Change password"}
          </h1>
          <p className="mb-4 text-label text-txt2">
            {forced
              ? "Passwords expire every 30 days. Set a new one to carry on."
              : "Choose something you have not used recently."}
          </p>
          <form onSubmit={submit} className="flex flex-col gap-3">
            {/* hint outside the label, linked by aria-describedby — see Signup.tsx */}
            <div>
              <label htmlFor="cp-cur" className="text-sm font-medium">Current password</label>
              <input id="cp-cur" required type="password" value={cur}
                onChange={(e) => setCur(e.target.value)} className={field} />
            </div>
            <div>
              <label htmlFor="cp-new" className="text-sm font-medium">New password</label>
              <input id="cp-new" required type="password" aria-describedby="cp-new-hint"
                value={next} onChange={(e) => setNext(e.target.value)} className={field} />
              <p id="cp-new-hint" className="mt-1 text-caption text-txt3">
                At least 8 characters, with letters and numbers. Your last 3 passwords cannot be reused.
              </p>
            </div>
            <div>
              <label htmlFor="cp-confirm" className="text-sm font-medium">Confirm new password</label>
              <input id="cp-confirm" required type="password" value={confirm}
                onChange={(e) => setConfirm(e.target.value)} className={field} />
            </div>
            {mismatch && <div className="text-label text-bad">Those two do not match.</div>}
            {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
            <button disabled={busy || !cur || !next || mismatch}
              className="btn btn-primary mt-1 justify-center disabled:opacity-50">
              {busy ? "Saving…" : "Change password"}
            </button>
          </form>
        </div>
        <button onClick={logout} className="mt-3 w-full text-center text-label text-txt3 hover:text-ink">
          Sign out instead
        </button>
      </div>
    </div>
  );
}
