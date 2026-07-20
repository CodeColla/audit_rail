import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function AuditorEntry() {
  const [params] = useSearchParams();
  const { enterAsGuest } = useAuth();
  const [err, setErr] = useState(false);

  useEffect(() => {
    const token = params.get("token");
    if (!token) { setErr(true); return; }
    enterAsGuest(token).catch(() => setErr(true));
  }, []);

  return (
    <div className="grid min-h-screen place-items-center bg-canvas text-center">
      {err ? (
        <div className="max-w-sm">
          <div className="text-[15px] font-semibold text-bad">This auditor link is invalid or expired.</div>
          <p className="mt-2 text-[13px] text-txt3">Ask your contact at the vendor to send a fresh invitation.</p>
        </div>
      ) : (
        <div className="text-[14px] text-txt2">Entering auditor review…</div>
      )}
    </div>
  );
}
