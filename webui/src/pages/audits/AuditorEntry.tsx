import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { AuthLayout } from "../../components/AuthLayout";

export default function AuditorEntry() {
  const [params] = useSearchParams();
  const { enterAsGuest } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState(false);

  useEffect(() => {
    const token = params.get("token");
    if (!token) { setErr(true); return; }
    // Leaving /auditor is not cosmetic, it is what completes the entry. App.tsx tests
    // `pathname.startsWith("/auditor")` BEFORE it tests `user.kind === "guest"`, so while
    // the URL still says /auditor the guest branch is unreachable and this component
    // re-renders itself — "Entering auditor review..." forever, with a valid session sitting
    // in localStorage. Every auditor invitation dead-ended here.
    enterAsGuest(token)
      .then(() => nav("/", { replace: true }))
      .catch(() => setErr(true));
  }, []);

  return (
    <AuthLayout>
      <div className="card p-6 text-center">
        {err ? (
          <>
            <div className="text-body font-semibold text-bad">This auditor link is invalid or expired.</div>
            <p className="mt-2 text-sm text-txt2">Ask your contact at the vendor to send a fresh invitation.</p>
          </>
        ) : (
          <div className="text-body text-txt2">Entering auditor review…</div>
        )}
      </div>
    </AuthLayout>
  );
}
