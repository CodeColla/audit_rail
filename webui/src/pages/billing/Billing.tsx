import { useQuery } from "@tanstack/react-query";
import { get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { Bar, Card, Loading, PageHead, Pill } from "../../lib/ui";

type Billing = {
  plan_code: "free" | "pro" | "enterprise";
  plan_name: string;
  in_grace: boolean;
  degraded: boolean;
  billing_cycle: "monthly" | "yearly" | null;
  status: "active" | "past_due" | "cancelled";
  current_period_end: string | null;
  grace_until: string | null;
  addon_audit_packs: number;
  addon_storage_packs: number;
  limits: { max_active_audits: number | null; storage_bytes: number | null; max_members: number | null };
  usage: { active_audits: number; storage_bytes: number; members: number };
};

type Plan = {
  name: string;
  max_active_audits: number | null;
  storage_bytes: number | null;
  max_members: number | null;
  price_paise: { monthly: number; yearly: number } | null;
};

type PlansResponse = { plans: Record<string, Plan>; addons: Record<string, unknown> };

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function formatINR(paise: number): string {
  return `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(paise / 100))}`;
}

/** `null` limit = unlimited — rendered as a plain dash, never "0%". */
function UsageRow({ label, used, max, format }:
  { label: string; used: number; max: number | null; format: (n: number) => string }) {
  const pct = max ? (used / max) * 100 : 0;
  const over = max !== null && used >= max;
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-sm">
        <span className="text-txt2">{label}</span>
        <span className={over ? "font-semibold text-bad" : "font-semibold text-ink"}>
          {format(used)} {max !== null && <span className="text-txt3">/ {format(max)}</span>}
          {max === null && <span className="text-txt3"> · Unlimited</span>}
        </span>
      </div>
      {max !== null && <Bar pct={pct} muted={!over} />}
    </div>
  );
}

export default function Billing() {
  const can = useCan();
  const canEdit = can("billing", "edit");
  const { data, isLoading } = useQuery({ queryKey: ["billing"], queryFn: () => get<Billing>("/billing") });
  const { data: catalogue } = useQuery({
    queryKey: ["billing", "plans"], queryFn: () => get<PlansResponse>("/billing/plans"),
  });

  if (isLoading || !data) return <Loading />;

  return (
    <>
      <PageHead eyebrow="Settings" title="Billing"
        lead="Your plan, usage and what upgrading would change. Phase 10 (req/phase10/00-issue-19.md) — self-serve checkout lands next." />

      {data.degraded && (
        <div className="mb-5 rounded-xl border border-bad bg-bad-bg px-4 py-3 text-sm text-bad">
          Your Pro subscription lapsed and the grace period has ended — this organisation is
          now running at Free plan limits. Upgrade again to restore Pro.
        </div>
      )}
      {data.in_grace && (
        <div className="mb-5 rounded-xl border border-warn bg-warn-bg px-4 py-3 text-sm text-warn">
          Your last payment failed. You're still on Pro limits until{" "}
          {data.grace_until?.slice(0, 10)} — update your payment method before then.
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.1fr_1fr]">
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="eyebrow">Current plan</div>
              <div className="mt-1 text-display font-semibold">{data.plan_name}</div>
            </div>
            <Pill tone={{ active: "ok", past_due: "warn", cancelled: "bad" }[data.status]}>
              {data.status.replace("_", " ")}
            </Pill>
          </div>
          <div className="flex flex-col gap-4">
            <UsageRow label="Active audits" used={data.usage.active_audits}
              max={data.limits.max_active_audits} format={(n) => String(n)} />
            <UsageRow label="Storage" used={data.usage.storage_bytes}
              max={data.limits.storage_bytes} format={formatBytes} />
            <UsageRow label="Logins" used={data.usage.members}
              max={data.limits.max_members} format={(n) => String(n)} />
          </div>
          {(data.addon_audit_packs > 0 || data.addon_storage_packs > 0) && (
            <p className="mt-4 text-caption text-txt3">
              Add-ons: {data.addon_audit_packs > 0 && `${data.addon_audit_packs}× audit pack`}
              {data.addon_audit_packs > 0 && data.addon_storage_packs > 0 && ", "}
              {data.addon_storage_packs > 0 && `${data.addon_storage_packs}× storage pack`}
            </p>
          )}
          {data.current_period_end && (
            <p className="mt-4 text-caption text-txt3">
              {data.status === "cancelled" ? "Access until" : "Renews"}{" "}
              {data.current_period_end.slice(0, 10)} ({data.billing_cycle})
            </p>
          )}
          <div className="mt-5 border-t border-bd pt-4">
            <button className="btn btn-primary disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canEdit || data.plan_code !== "free"}
              title="Self-serve checkout isn't live yet — see docs/phase10/01-sprint-plan.md S3">
              {data.plan_code === "free" ? "Upgrade to Pro" : "Manage plan"}
            </button>
            {!canEdit && (
              <p className="mt-2 text-caption text-txt3">
                Only an organisation admin can change the plan.
              </p>
            )}
          </div>
        </Card>

        <Card className="p-5">
          <div className="eyebrow mb-3">Plans</div>
          <div className="flex flex-col gap-3">
            {catalogue && Object.entries(catalogue.plans).map(([code, plan]) => (
              <div key={code}
                className={`flex items-center justify-between rounded-lg border px-3.5 py-3 ${
                  code === data.plan_code ? "border-accent bg-[rgba(249,115,22,0.06)]" : "border-bd"}`}>
                <div>
                  <div className="text-sm font-semibold">{plan.name}</div>
                  <div className="mt-0.5 text-caption text-txt3">
                    {plan.max_active_audits ?? "Unlimited"} audits ·{" "}
                    {plan.storage_bytes ? formatBytes(plan.storage_bytes) : "Unlimited"} storage
                  </div>
                </div>
                <div className="text-right text-sm font-semibold">
                  {plan.price_paise ? formatINR(plan.price_paise.monthly) + "/mo" : "Custom"}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
