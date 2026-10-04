import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { openCheckout } from "../../lib/razorpay";
import { Bar, Card, Loading, Modal, PageHead, Pill } from "../../lib/ui";

type LicenseStatus = "unlicensed" | "unverified" | "grace" | "active"
  | "frozen_missing" | "frozen_invalid" | "frozen_expired";

type Billing = {
  deployment_mode: "public" | "enterprise";
  plan_code: "free" | "pro" | "enterprise";
  plan_name: string;
  in_grace: boolean;
  degraded: boolean;
  limits: { max_active_audits: number | null; storage_bytes: number | null; max_members: number | null };
  usage: { active_audits: number; storage_bytes: number; members: number };
  // Public mode only:
  billing_cycle?: "monthly" | "yearly" | null;
  status?: "active" | "past_due" | "cancelled";
  current_period_end?: string | null;
  grace_until?: string | null;
  addon_audit_packs?: number;
  addon_storage_packs?: number;
  // Enterprise mode only:
  license?: {
    status: LicenseStatus;
    customer: string | null;
    issued_at: string | null;
    expires_at: string | null;
    applied_at: string | null;
  };
};

type Plan = {
  name: string;
  max_active_audits: number | null;
  storage_bytes: number | null;
  max_members: number | null;
  price_paise: { monthly: number; yearly: number } | null;
};

type PlansResponse = { plans: Record<string, Plan>; addons: Record<string, { name: string; price_paise: Record<string, number> }> };

type CheckoutSub = { id: string; item: string; short_url?: string; needs_checkout: boolean };
type CheckoutResponse = { key_id: string; subscriptions: CheckoutSub[]; prefill: { name: string; email: string } };
type Invoice = { date: string | null; amount: number; status: string; short_url: string | null };

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function formatINR(paise: number): string {
  return `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(paise / 100))}`;
}

/** Runs every subscription a checkout/addons call returned through the Razorpay modal in
 * turn (Pro first — the backend already orders them that way), verifying each as it
 * completes. Subscriptions with `needs_checkout: false` (an existing one whose quantity
 * was just PATCHed, no payment needed) are skipped. */
async function completeCheckouts(subs: CheckoutSub[], keyId: string,
                                 prefill: { name: string; email: string }): Promise<void> {
  for (const sub of subs) {
    if (!sub.needs_checkout) continue;
    const result = await openCheckout({ key: keyId, subscriptionId: sub.id, prefill });
    await api.post("/billing/verify", {
      razorpay_payment_id: result.razorpay_payment_id,
      razorpay_subscription_id: result.razorpay_subscription_id,
      razorpay_signature: result.razorpay_signature,
    });
  }
}

/** Polls GET /billing until the plan actually shows as paid, or gives up after ~60s — the
 * webhook is the real source of truth and can lag a verify() call by a few seconds. */
async function waitForPlan(wantPro: boolean, deadlineMs = 60_000): Promise<Billing> {
  const start = Date.now();
  while (true) {
    const b = await get<Billing>("/billing");
    if (wantPro ? b.plan_code !== "free" : true) return b;
    if (Date.now() - start > deadlineMs) return b;
    await new Promise((r) => setTimeout(r, 3000));
  }
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

function Stepper({ label, value, onChange, hint }:
  { label: string; value: number; onChange: (n: number) => void; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-bd px-3.5 py-2.5">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-caption text-txt3">{hint}</div>}
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => onChange(Math.max(0, value - 1))}
          className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:bg-canvas">−</button>
        <span className="w-6 text-center text-sm font-semibold tnum">{value}</span>
        <button type="button" onClick={() => onChange(value + 1)}
          className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:bg-canvas">+</button>
      </div>
    </div>
  );
}

const LICENSE_PILL: Record<LicenseStatus, string> = {
  active: "ok", grace: "warn", unlicensed: "na", unverified: "warn",
  frozen_missing: "bad", frozen_invalid: "bad", frozen_expired: "bad",
};

const LICENSE_LABEL: Record<LicenseStatus, string> = {
  active: "Active", grace: "Expired — in grace", unlicensed: "No license loaded",
  unverified: "Could not be verified", frozen_missing: "Frozen — no license",
  frozen_invalid: "Frozen — invalid license", frozen_expired: "Frozen — license expired",
};

function ApplyLicenseForm() {
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const apply = useMutation({
    mutationFn: () => api.put("/license", { token }),
    onSuccess: () => { setToken(""); setErr(""); qc.invalidateQueries({ queryKey: ["billing"] }); },
    onError: (e: any) => setErr(errText(e, "Could not apply that license.")),
  });
  return (
    <div className="mt-5 border-t border-bd pt-4">
      <label className="text-caption font-semibold text-txt2">Apply a license token</label>
      <textarea value={token} onChange={(e) => setToken(e.target.value)} rows={3}
        placeholder="Paste the token SR Associates sent you"
        className="mt-2 w-full resize-none rounded-md border border-bd bg-paper px-3 py-2 font-mono text-xs" />
      {err && <p className="mt-2 text-sm text-bad">{err}</p>}
      <button onClick={() => apply.mutate()} disabled={!token.trim() || apply.isPending}
        className="btn btn-primary mt-3 disabled:cursor-not-allowed disabled:opacity-50">
        {apply.isPending ? "Applying…" : "Apply license"}
      </button>
    </div>
  );
}

type Cycle = "monthly" | "yearly";

/** The whole self-serve purchase flow: pick a cycle and add-ons, run every checkout the
 * backend hands back through the Razorpay modal in turn, verify each, then wait for the
 * real plan to show up (the webhook is the source of truth — this is just UI patience). */
function UpgradePanel({ catalogue, onDone }: { catalogue?: PlansResponse; onDone: () => void }) {
  const qc = useQueryClient();
  const [cycle, setCycle] = useState<Cycle>("monthly");
  const [addonAudits, setAddonAudits] = useState(0);
  const [addonStorage, setAddonStorage] = useState(0);
  const [phase, setPhase] = useState<"idle" | "starting" | "paying" | "waiting">("idle");
  const [error, setError] = useState("");

  const pro = catalogue?.plans.pro;
  const addons = catalogue?.addons;
  const price = pro?.price_paise?.[cycle];

  async function start() {
    setError(""); setPhase("starting");
    try {
      const { data } = await api.post<CheckoutResponse>("/billing/checkout", {
        cycle, addon_audit_packs: addonAudits, addon_storage_packs: addonStorage,
      });
      setPhase("paying");
      await completeCheckouts(data.subscriptions, data.key_id, data.prefill);
      setPhase("waiting");
      await waitForPlan(true);
      // waitForPlan() polls with a raw get() — real and correct against the backend, but
      // invisible to the page's OWN useQuery(["billing"]) cache. Without this, the panel
      // closes but the current-plan card keeps showing stale "Free" until some unrelated
      // navigation happens to refetch it.
      qc.invalidateQueries({ queryKey: ["billing"] });
      onDone();
    } catch (e: any) {
      setError(errText(e, "Something went wrong during checkout — nothing was charged if "
                        + "you didn't see a payment screen."));
      setPhase("idle");
    }
  }

  if (phase === "paying" || phase === "waiting" || phase === "starting") {
    return (
      <div className="mt-5 border-t border-bd pt-5 text-center">
        <p className="text-sm font-medium text-ink">
          {phase === "starting" && "Starting checkout…"}
          {phase === "paying" && "Complete the payment in the window that opened…"}
          {phase === "waiting" && "Waiting for payment confirmation…"}
        </p>
        <p className="mt-1 text-caption text-txt3">This can take a few seconds after you pay.</p>
      </div>
    );
  }

  return (
    <div className="mt-5 border-t border-bd pt-5">
      <div className="mb-4 flex justify-center">
        <div className="inline-flex rounded-xl border border-bd bg-paper p-1">
          {(["monthly", "yearly"] as Cycle[]).map((c) => (
            <button key={c} type="button" onClick={() => setCycle(c)}
              className={`rounded-lg px-4 py-1.5 text-sm font-semibold transition-colors ${
                cycle === c ? "bg-ink text-paper" : "text-txt2 hover:text-ink"}`}>
              {c === "monthly" ? "Monthly" : "Yearly"}
              {c === "yearly" && <span className="ml-1.5 text-accent">· 2 months free</span>}
            </button>
          ))}
        </div>
      </div>

      {price != null && (
        <p className="mb-4 text-center text-sm text-txt2">
          Pro: <span className="font-semibold text-ink">{formatINR(price)}</span> / {cycle === "monthly" ? "mo" : "yr"}
        </p>
      )}

      {addons && (
        <div className="flex flex-col gap-2">
          <Stepper label={addons.audits.name} value={addonAudits} onChange={setAddonAudits}
            hint={`${formatINR(addons.audits.price_paise[cycle])}/${cycle === "monthly" ? "mo" : "yr"} each`} />
          <Stepper label={addons.storage.name} value={addonStorage} onChange={setAddonStorage}
            hint={`${formatINR(addons.storage.price_paise[cycle])}/${cycle === "monthly" ? "mo" : "yr"} each`} />
        </div>
      )}

      {cycle === "yearly" && (
        <p className="mt-3 text-caption text-txt3">
          Your bank may ask you to approve each yearly renewal — an RBI rule for recurring
          payments above ₹15,000.
        </p>
      )}
      {error && <p className="mt-3 text-sm text-bad">{error}</p>}

      <button onClick={start} className="btn btn-primary mt-4 w-full justify-center">
        Continue to payment
      </button>
    </div>
  );
}

function CancelModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const cancel = useMutation({
    mutationFn: () => api.post("/billing/cancel"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["billing"] }); onClose(); },
    onError: (e: any) => setError(errText(e, "Could not cancel.")),
  });
  return (
    <Modal open={open} onClose={onClose} title="Cancel Pro?">
      <p className="text-sm text-txt2">
        You'll keep Pro's limits until the end of the period you've already paid for. Monthly
        payments aren't refunded; a yearly payment is fully refunded if you cancel within 7
        days of paying — see the Refund &amp; Cancellation policy.
      </p>
      {error && <p className="mt-3 text-sm text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className="btn">Keep Pro</button>
        <button onClick={() => cancel.mutate()} disabled={cancel.isPending}
          className="rounded-md border border-bad bg-bad-bg px-3.5 py-2 text-sm font-semibold text-bad disabled:opacity-50">
          {cancel.isPending ? "Cancelling…" : "Cancel subscription"}
        </button>
      </div>
    </Modal>
  );
}

/** Shown once Pro is active: change add-ons, cancel, see invoices. */
function ManagePlan({ data }: { data: Billing }) {
  const qc = useQueryClient();
  const [addonAudits, setAddonAudits] = useState(data.addon_audit_packs ?? 0);
  const [addonStorage, setAddonStorage] = useState(data.addon_storage_packs ?? 0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const { data: invoices } = useQuery({
    queryKey: ["billing", "invoices"], queryFn: () => get<{ invoices: Invoice[] }>("/billing/invoices"),
  });

  const dirty = addonAudits !== (data.addon_audit_packs ?? 0)
    || addonStorage !== (data.addon_storage_packs ?? 0);

  async function save() {
    setError(""); setSaving(true);
    try {
      const { data: resp } = await api.post<CheckoutResponse & { subscriptions: CheckoutSub[] }>(
        "/billing/addons", { addon_audit_packs: addonAudits, addon_storage_packs: addonStorage });
      await completeCheckouts(resp.subscriptions, resp.key_id, resp.prefill);
      qc.invalidateQueries({ queryKey: ["billing"] });
    } catch (e: any) {
      setError(errText(e, "Could not update add-ons."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-5 border-t border-bd pt-4">
      <div className="eyebrow mb-2">Add-ons</div>
      <div className="flex flex-col gap-2">
        <Stepper label="+5 active audits" value={addonAudits} onChange={setAddonAudits} />
        <Stepper label="+25 GB storage" value={addonStorage} onChange={setAddonStorage} />
      </div>
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      {dirty && (
        <button onClick={save} disabled={saving} className="btn btn-primary mt-3 disabled:opacity-50">
          {saving ? "Saving…" : "Save add-ons"}
        </button>
      )}

      <div className="mt-5 flex items-center justify-between">
        <div className="eyebrow">Invoices</div>
        <button onClick={() => setCancelOpen(true)} className="text-caption text-bad hover:underline">
          Cancel Pro
        </button>
      </div>
      {!invoices || invoices.invoices.length === 0 ? (
        <p className="mt-2 text-caption text-txt3">No invoices yet.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {invoices.invoices.map((inv, i) => (
            <li key={i} className="flex items-center justify-between text-sm">
              <span className="text-txt2">{inv.date?.slice(0, 10) ?? "—"}</span>
              <span className="font-medium">{formatINR(inv.amount)}</span>
              {inv.short_url ? (
                <a href={inv.short_url} target="_blank" rel="noopener noreferrer"
                  className="text-caption text-accent hover:underline">View</a>
              ) : <span className="text-caption text-txt3">{inv.status}</span>}
            </li>
          ))}
        </ul>
      )}
      <CancelModal open={cancelOpen} onClose={() => setCancelOpen(false)} />
    </div>
  );
}

export default function Billing() {
  const can = useCan();
  const canEdit = can("billing", "edit");
  const [params, setParams] = useSearchParams();
  const [showUpgrade, setShowUpgrade] = useState(false);

  const { data, isLoading } = useQuery({ queryKey: ["billing"], queryFn: () => get<Billing>("/billing") });
  const enterprise = data?.deployment_mode === "enterprise";
  const { data: catalogue } = useQuery({
    queryKey: ["billing", "plans"], queryFn: () => get<PlansResponse>("/billing/plans"),
    enabled: !enterprise,
  });

  useEffect(() => {
    if (params.get("upgrade") === "pro" && data?.plan_code === "free") {
      setShowUpgrade(true);
      const next = new URLSearchParams(params);
      next.delete("upgrade");
      setParams(next, { replace: true });
    }
  }, [params, data?.plan_code]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading || !data) return <Loading />;

  return (
    <>
      <PageHead eyebrow="Settings" title={enterprise ? "License" : "Billing"}
        lead={enterprise
          ? "This deployment's enterprise license and usage against it."
          : "Your plan, usage and billing."} />

      {/* Phase 10: the grace/degraded message itself lives in BillingBanner (Shell.tsx) —
          it shows on every page, including this one, so it isn't repeated here. This
          page only adds what BillingBanner can't: the actual fix (apply a license /
          upgrade), below. */}

      <div className={enterprise ? "max-w-xl" : "grid grid-cols-1 gap-5 lg:grid-cols-[1.1fr_1fr]"}>
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="eyebrow">{enterprise ? "License" : "Current plan"}</div>
              <div data-testid="current-plan-name" className="mt-1 text-display font-semibold">
                {enterprise ? (data.license?.customer ?? "Enterprise") : data.plan_name}
              </div>
            </div>
            {enterprise ? (
              <Pill tone={LICENSE_PILL[data.license!.status]}>{LICENSE_LABEL[data.license!.status]}</Pill>
            ) : (
              <Pill tone={{ active: "ok", past_due: "warn", cancelled: "bad" }[data.status!]}>
                {data.status!.replace("_", " ")}
              </Pill>
            )}
          </div>
          <div className="flex flex-col gap-4">
            <UsageRow label="Active audits" used={data.usage.active_audits}
              max={data.limits.max_active_audits} format={(n) => String(n)} />
            <UsageRow label="Storage" used={data.usage.storage_bytes}
              max={data.limits.storage_bytes} format={formatBytes} />
            <UsageRow label="Logins" used={data.usage.members}
              max={data.limits.max_members} format={(n) => String(n)} />
          </div>

          {!enterprise && (data.addon_audit_packs! > 0 || data.addon_storage_packs! > 0) && (
            <p className="mt-4 text-caption text-txt3">
              Add-ons: {data.addon_audit_packs! > 0 && `${data.addon_audit_packs}× audit pack`}
              {data.addon_audit_packs! > 0 && data.addon_storage_packs! > 0 && ", "}
              {data.addon_storage_packs! > 0 && `${data.addon_storage_packs}× storage pack`}
            </p>
          )}
          {!enterprise && data.current_period_end && (
            <p className="mt-4 text-caption text-txt3">
              {data.status === "cancelled" ? "Access until" : "Renews"}{" "}
              {data.current_period_end.slice(0, 10)} ({data.billing_cycle})
            </p>
          )}
          {enterprise && data.license?.expires_at && (
            <p className="mt-4 text-caption text-txt3">
              {data.license.status === "grace" || data.license.status === "frozen_expired"
                ? "Expired" : "Valid until"} {data.license.expires_at.slice(0, 10)}
            </p>
          )}

          {enterprise ? (
            canEdit ? <ApplyLicenseForm /> : (
              <p className="mt-5 border-t border-bd pt-4 text-caption text-txt3">
                Only an organisation admin can apply a license.
              </p>
            )
          ) : data.plan_code === "pro" ? (
            canEdit ? <ManagePlan data={data} /> : (
              <p className="mt-5 border-t border-bd pt-4 text-caption text-txt3">
                Only an organisation admin can manage billing.
              </p>
            )
          ) : (
            <>
              <div className="mt-5 border-t border-bd pt-4">
                <button onClick={() => setShowUpgrade((v) => !v)} disabled={!canEdit}
                  className="btn btn-primary disabled:cursor-not-allowed disabled:opacity-50">
                  Upgrade to Pro
                </button>
                {!canEdit && (
                  <p className="mt-2 text-caption text-txt3">
                    Only an organisation admin can change the plan.
                  </p>
                )}
              </div>
              {canEdit && showUpgrade && (
                <UpgradePanel catalogue={catalogue} onDone={() => setShowUpgrade(false)} />
              )}
            </>
          )}
        </Card>

        {!enterprise && (
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
        )}
      </div>
    </>
  );
}
