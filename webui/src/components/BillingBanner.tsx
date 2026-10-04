import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { get } from "../lib/api";
import { useCan } from "../lib/auth";

type BillingSummary = {
  deployment_mode: "public" | "enterprise";
  in_grace: boolean;
  degraded: boolean;
  grace_until?: string | null;
  license?: { expires_at: string | null };
};

/**
 * Phase 10 — a thin strip between the header and the page content, shown only when
 * there's something billing-shaped an admin needs to know about: a failed payment still
 * inside its grace period, or usage already frozen because grace ran out. Shares its
 * query (and cache) with Billing.tsx's own `useQuery(["billing"], ...)` — a mutation
 * there invalidating that key refreshes this banner too, with no extra wiring.
 */
export function BillingBanner() {
  const can = useCan();
  const { data } = useQuery({
    queryKey: ["billing"],
    queryFn: () => get<BillingSummary>("/billing"),
    enabled: can("billing", "view"),
    staleTime: 5 * 60 * 1000,
  });

  if (!data || (!data.in_grace && !data.degraded)) return null;

  const enterprise = data.deployment_mode === "enterprise";
  const tone = data.degraded
    ? "border-bad bg-bad-bg text-bad"
    : "border-warn bg-warn-bg text-warn";
  const message = data.degraded
    ? enterprise
      ? "This deployment's license has lapsed — usage is frozen at its current level."
      : "Your organisation is on Free limits — your Pro subscription lapsed."
    : enterprise
      ? `Your license expired on ${data.license?.expires_at?.slice(0, 10)} — still running at full limits during the grace period.`
      : `Your last payment failed — you're still on Pro limits until ${data.grace_until?.slice(0, 10)}.`;

  return (
    <div className={`border-b px-6 py-2 text-center text-sm ${tone}`}>
      {message}{" "}
      <Link to="/billing" className="font-semibold underline">
        {enterprise ? "Apply a license" : "Go to Billing"}
      </Link>
    </div>
  );
}
