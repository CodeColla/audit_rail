import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { errText } from "../lib/api";

/**
 * Create an account and its organisation in one step — the signer becomes its Super Admin.
 *
 * The GST number is **optional** as of P5-S6. It used to be required and checksum-validated,
 * which turned a nice-to-have identifier into a wall: anyone without the number to hand — a
 * new entity, a non-Indian arm, or just someone signing up before finance sends it over —
 * could not create an account at all. It is still unique when supplied, so it still does the
 * one job worth keeping: stopping the same company being registered twice.
 */
export default function Signup() {
  const { signup } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({ full_name: "", email: "", password: "",
    organisation_name: "", gst_number: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const field = "mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await signup(f);
      nav("/");
    } catch (e: any) {
      setErr(errText(e, "Could not create the account."));
    } finally {
      setBusy(false);
    }
  }

  // Deliberately NOT gated on gst_number — see the note at the top of the file.
  const ready = f.full_name && f.email && f.password && f.organisation_name;

  return (
    <div className="grid min-h-screen place-items-center bg-canvas px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-2">
          <span className="text-[28px] font-bold tracking-[-0.04em] text-ink">SR</span>
          <span className="mb-3.5 h-[11px] w-[11px] rounded-[2px] bg-accent" />
          <span className="ml-1 font-mono text-[13px] text-txt3">audit_rail</span>
        </div>
        <div className="card p-6">
          <div className="eyebrow">Get started</div>
          <h1 className="mb-1 mt-1 text-[20px] font-semibold">Create your organisation</h1>
          <p className="mb-4 text-[12.5px] text-txt2">
            You'll be its Super Admin — you can invite your compliance team afterwards.
          </p>
          <form onSubmit={submit} className="flex flex-col gap-3">
            {/* Hints sit OUTSIDE the label and are linked with aria-describedby: nesting
                them made the accessible name "Password At least 8 characters…", which is
                both wrong for screen readers and unmatchable by label. */}
            <div>
              <label htmlFor="su-name" className="text-[13px] font-medium">Your name</label>
              <input id="su-name" required value={f.full_name} onChange={set("full_name")} className={field} />
            </div>
            <div>
              <label htmlFor="su-email" className="text-[13px] font-medium">Work email</label>
              <input id="su-email" required type="email" value={f.email} onChange={set("email")} className={field} />
            </div>
            <div>
              <label htmlFor="su-pw" className="text-[13px] font-medium">Password</label>
              <input id="su-pw" required type="password" aria-describedby="su-pw-hint"
                value={f.password} onChange={set("password")} className={field} />
              <p id="su-pw-hint" className="mt-1 text-[11px] text-txt3">
                At least 8 characters, with both letters and numbers. It expires every 30 days.
              </p>
            </div>
            <hr className="my-1 border-bd" />
            <div>
              <label htmlFor="su-org" className="text-[13px] font-medium">Organisation name</label>
              <input id="su-org" required value={f.organisation_name} onChange={set("organisation_name")}
                className={field} placeholder="KIAM INTL PVT LTD" />
            </div>
            <div>
              <label htmlFor="su-gst" className="text-[13px] font-medium">
                GST number <span className="font-normal text-txt3">(optional)</span>
              </label>
              <input id="su-gst" value={f.gst_number} onChange={set("gst_number")}
                aria-describedby="su-gst-hint"
                className={field + " font-mono uppercase"} placeholder="27AAPFU0939F1ZV" />
              <p id="su-gst-hint" className="mt-1 text-[11px] text-txt3">
                Add it if you have it — you can leave this blank and fill it in later. One GST
                number, one organisation, so it also stops a duplicate signup.
              </p>
            </div>
            {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
            <button disabled={!ready || busy} className="btn btn-primary mt-1 justify-center disabled:opacity-50">
              {busy ? "Creating…" : "Create organisation"}
            </button>
          </form>
        </div>
        <p className="mt-3 text-center text-[12px] text-txt3">
          Already have an account? <Link to="/login" className="underline">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
