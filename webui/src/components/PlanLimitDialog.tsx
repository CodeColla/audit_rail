import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useCan } from "../lib/auth";
import { Modal } from "../lib/ui";

type PlanLimitDetail = {
  code: "plan_limit";
  limit: "audits" | "storage" | "members";
  used: number;
  max: number;
  message: string;
};

const TITLES: Record<PlanLimitDetail["limit"], string> = {
  audits: "Active audit limit reached",
  storage: "Storage limit reached",
  members: "Login limit reached",
};

/**
 * Mounted once, in Shell. Any API call that 402s with a `plan_limit` detail
 * (api/domain/entitlements.py) is broadcast as a window event (lib/api.ts) rather than
 * handled at the call site, so every blocked action — creating an audit, an upload,
 * inviting a login — surfaces the same dialog instead of a one-off inline error.
 */
export function PlanLimitDialog() {
  const [detail, setDetail] = useState<PlanLimitDetail | null>(null);
  const can = useCan();

  useEffect(() => {
    const onLimit = (e: Event) => setDetail((e as CustomEvent<PlanLimitDetail>).detail);
    window.addEventListener("ar:plan-limit", onLimit);
    return () => window.removeEventListener("ar:plan-limit", onLimit);
  }, []);

  if (!detail) return null;
  const canManageBilling = can("billing", "edit");

  return (
    <Modal open onClose={() => setDetail(null)} title={TITLES[detail.limit]}>
      <p className="text-sm text-txt2">{detail.message}</p>
      {!canManageBilling && (
        <p className="mt-3 text-caption text-txt3">
          Ask your organisation admin to upgrade your plan.
        </p>
      )}
      <div className="mt-5 flex items-center justify-end gap-2">
        <button onClick={() => setDetail(null)} className="btn">
          {canManageBilling ? "Not now" : "Okay"}
        </button>
        {canManageBilling && (
          <Link to="/billing" onClick={() => setDetail(null)} className="btn btn-primary">
            Upgrade
          </Link>
        )}
      </div>
    </Modal>
  );
}
