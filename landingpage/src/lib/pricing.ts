// Mirrors api/domain/plans.py (Phase 10, S2a) — the backend is what's actually enforced;
// this file only has to agree with it on what people see before they sign up. If a price or
// limit changes, change both, or the landing page is lying. See
// req/phase10/00-issue-19.md §1 for the numbers and the reasoning behind them.
export type BillingCycle = "monthly" | "yearly";

export interface PlanLimits {
  activeAudits: string;
  storage: string;
  members: string;
  guests: string;
}

export interface Plan {
  code: "free" | "pro" | "enterprise";
  name: string;
  /** Paise. `null` means "priced per deal", not shown as a number. */
  price: Record<BillingCycle, number | null>;
  priceNote?: string;
  limits: PlanLimits;
  support: string;
  highlight?: boolean;
}

// Becomes "+ GST" once SR Associates registers for GST — req/phase10/00-issue-19.md §1.
// Empty for now: SR Associates is a sole proprietorship under the registration threshold,
// so the prices below are the final amount a customer pays.
export const GST_NOTE = "";

export const PLANS: Plan[] = [
  {
    code: "free",
    name: "Free",
    price: { monthly: 0, yearly: 0 },
    priceNote: "forever, no card needed",
    limits: { activeAudits: "1", storage: "500 MB", members: "3", guests: "Unlimited" },
    support: "Email (best effort)",
  },
  {
    code: "pro",
    name: "Pro",
    price: { monthly: 249_900, yearly: 2_499_000 },
    priceNote: "billed per organisation",
    limits: { activeAudits: "10", storage: "25 GB", members: "Unlimited", guests: "Unlimited" },
    support: "Email, 1 business day",
    highlight: true,
  },
  {
    code: "enterprise",
    name: "Enterprise",
    price: { monthly: null, yearly: null },
    priceNote: "priced per deal",
    limits: { activeAudits: "Contract", storage: "Contract", members: "Unlimited", guests: "Unlimited" },
    support: "Dedicated CSM",
  },
];

export interface Addon {
  key: "audits" | "storage";
  label: string;
  price: Record<BillingCycle, number>;
}

export const ADDONS: Addon[] = [
  { key: "audits", label: "+5 active audits", price: { monthly: 99_900, yearly: 999_000 } },
  { key: "storage", label: "+25 GB storage", price: { monthly: 49_900, yearly: 499_000 } },
];

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** `paise` → "₹2,499". Rupees only — nobody needs to see paise on a pricing page. */
export function formatINR(paise: number): string {
  return `₹${inr.format(Math.round(paise / 100))}`;
}
