// Razorpay Standard Checkout (Phase 10, S3b) — req/phase10/00-issue-19.md,
// docs/phase10/01-sprint-plan.md.
//
// Pro is a recurring Subscription, so this opens checkout.js with a subscription_id, not
// an order_id — the modal and the signature fields it hands back are the same shape
// either way, just with razorpay_subscription_id instead of razorpay_order_id.

declare global {
  interface Window {
    Razorpay: new (options: Record<string, unknown>) => { open: () => void };
  }
}

let checkoutScript: Promise<void> | null = null;

/** Injects checkout.js once, however many times this is called across the page's life. */
export function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  if (checkoutScript) return checkoutScript;
  checkoutScript = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Could not load the payment widget — check your connection."));
    document.body.appendChild(script);
  });
  return checkoutScript;
}

export type CheckoutResult = {
  razorpay_payment_id: string;
  razorpay_subscription_id: string;
  razorpay_signature: string;
};

/**
 * Opens the modal for one subscription and resolves with the signed result once the
 * customer completes it. Rejects on a dismissed modal or a failed payment — the caller
 * decides what that means (e.g. stop here vs. let them retry).
 */
export async function openCheckout({ key, subscriptionId, prefill }: {
  key: string; subscriptionId: string; prefill: { name: string; email: string };
}): Promise<CheckoutResult> {
  await loadCheckout();
  return new Promise((resolve, reject) => {
    const rzp = new window.Razorpay({
      key,
      subscription_id: subscriptionId,
      name: "SR Associates",
      description: "Auditrail Pro",
      prefill,
      handler: (response: CheckoutResult) => resolve(response),
      modal: { ondismiss: () => reject(new Error("Payment window closed.")) },
    }) as any;
    rzp.on("payment.failed", (resp: any) => {
      reject(new Error(resp?.error?.description || "The payment failed. Nothing was charged."));
    });
    rzp.open();
  });
}
