import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { DocBody } from "../components/DocBody";
import { Wordmark } from "../components/Brand";
import { inputCls } from "../lib/ui";

/**
 * The public, mobile-first signing page reached from a magic link (`/sign/:token`).
 * Deliberately standalone: no Shell, no auth, plain `fetch` (never the token-carrying
 * axios instance) so a logged-out field engineer can read a policy and sign it.
 */

type Page = {
  kind: string; document_title: string; classification: string; version_label: string;
  content: string; content_format: "MARKDOWN" | "HTML" | "SHEET";
  consent_text: string; signer_name: string; signer_email: string;
};
type View = "loading" | "ready" | "gone" | "done";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-bd bg-paper px-4 py-3">
        {/* The one page a customer's own employee sees, usually on a phone, from an
            emailed link — so it carries the real wordmark rather than an improvised tile. */}
        <div className="mx-auto flex max-w-2xl items-center gap-2">
          <Wordmark size="sm" />
          <span className="text-body text-txt2">· Policy attestation</span>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-6">{children}</main>
    </div>
  );
}

export default function Sign() {
  const { token } = useParams();
  const [view, setView] = useState<View>("loading");
  const [page, setPage] = useState<Page | null>(null);
  const [msg, setMsg] = useState("");
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [signedAt, setSignedAt] = useState("");

  useEffect(() => {
    let live = true;
    fetch(`/api/sign/${token}`)
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!live) return;
        if (r.ok) { setPage(d); setName(d.signer_name ?? ""); setView("ready"); }
        else { setMsg(d.detail ?? "This signing link is not valid."); setView("gone"); }
      })
      .catch(() => { if (live) { setMsg("Could not reach the server."); setView("gone"); } });
    return () => { live = false; };
  }, [token]);

  const sign = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await fetch(`/api/sign/${token}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signer_name: name, agree }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) { setSignedAt(d.signed_at ?? ""); setView("done"); }
      else {
        setMsg(d.detail ?? "Could not record your signature.");
        if (r.status === 410 || r.status === 404) setView("gone");
      }
    } finally { setBusy(false); }
  };

  if (view === "loading")
    return <Frame><div className="p-8 text-center text-sm text-txt3">Loading…</div></Frame>;

  if (view === "gone")
    return (
      <Frame>
        <div className="card p-8 text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-na-bg text-na">!</div>
          <h1 className="text-subtitle font-semibold">This link can’t be used</h1>
          <p className="mx-auto mt-2 max-w-[40ch] text-sm text-txt2">{msg}</p>
          <p className="mt-3 text-label text-txt3">If you still need to sign, ask your compliance team to send a fresh link.</p>
        </div>
      </Frame>
    );

  if (view === "done")
    return (
      <Frame>
        <div className="card p-8 text-center">
          <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-ok-bg text-title text-ok">✓</div>
          <h1 className="text-subtitle font-semibold">Signed — thank you</h1>
          <p className="mx-auto mt-2 max-w-[46ch] text-sm text-txt2">
            Your attestation for <b>{page?.document_title}</b> (v{page?.version_label}) has been recorded
            {signedAt && <> on {signedAt.slice(0, 10)}</>}. You can close this page.
          </p>
        </div>
      </Frame>
    );

  // ready
  return (
    <Frame>
      <div className="mb-3">
        <div className="text-caption font-semibold uppercase tracking-[0.12em] text-txt3">{page?.classification}</div>
        <h1 className="mt-0.5 text-title font-semibold tracking-[-0.01em]">{page?.document_title}</h1>
        <div className="text-label text-txt2">Version {page?.version_label} · to be signed by {page?.signer_email}</div>
      </div>

      <DocBody content={page?.content ?? ""} format={page?.content_format}
        className="card max-h-[52vh] overflow-y-auto p-5" />

      <div className="card mt-4 p-5">
        <p className="text-sm leading-relaxed text-ink">{page?.consent_text}</p>
        <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-sm">
          <input type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>I confirm I have read and understood this document and agree to comply with it.</span>
        </label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Type your full name to sign" className={inputCls + " mt-3"} />
        {msg && <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{msg}</div>}
        <button disabled={!agree || !name.trim() || busy} onClick={sign}
          className="btn btn-primary mt-4 w-full justify-center disabled:opacity-50">
          {busy ? "Signing…" : "Sign"}
        </button>
        <p className="mt-2 text-center text-caption text-txt3">
          Your name, the time, and your device details are recorded as your electronic signature.
        </p>
      </div>
    </Frame>
  );
}
