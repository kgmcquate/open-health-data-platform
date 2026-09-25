import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { createCheckoutSession } from "../lib/api";

const FEATURES = [
  "50 questions a day (free: 10)",
  "2 million tokens a day (free: 200k)",
  "300 dashboard renders a day (free: 30)",
  "20 dashboard publishes a day (free: 2)",
  "3 issue reports a day (free: 1)",
];

// --- Stripe.js (the v3 build, loaded from index.html) ----------------------
// The embedded Checkout page is rendered by Stripe.js' embedded Checkout:
//   Stripe(publishableKey) -> initEmbeddedCheckout({ clientSecret }) -> mount("#checkout-form")
// Typed loosely here — it is a GA API reached through a global script, not a
// package we can import. The publishable key is browser-accessible, so it
// MUST come from a VITE_-prefixed env var (baked in at `vite build` time).
const PUBLISHABLE_KEY = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY as
  | string
  | undefined;

type StripeGlobal = {
  Stripe?: (publishableKey: string) => StripeSdk;
};
type StripeSdk = {
  initEmbeddedCheckout: (options: {
    clientSecret: string;
  }) => Promise<StripeCheckout>;
};
type StripeCheckout = {
  mount: (selector: string) => void;
  on: (event: "onComplete", handler: () => void) => void;
};

/** The Pro plans page (`hub_api.billing`): a $5/mo Stripe subscription sold via
 * Stripe's **embedded Checkout** page. The server hands back a `client_secret`;
 * `initEmbeddedCheckout` mounts the Stripe-hosted Checkout page into
 * `#checkout-form` below and the purchase completes on this origin (the server
 * disables the post-payment redirect, so `onComplete` is what flips the UI to
 * the "Thanks" state).
 *
 * The numbers in `FEATURES` are the paid-tier caps the server enforces — the
 * public plan copy, not a quota source of truth. */
export default function Billing() {
  const { user, signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const start = async () => {
    setError(null);
    setSubmitted(false);

    const { Stripe } = window as unknown as StripeGlobal;
    if (!PUBLISHABLE_KEY) {
      setError("Stripe isn't configured on this deployment.");
      return;
    }
    if (!Stripe) {
      setError("Stripe.js didn't load — check your network and the script tag.");
      return;
    }

    setLoading(true);
    try {
      // 1. Ask the server for an embedded Checkout Session (client_secret).
      const { client_secret } = await createCheckoutSession();
      // 2. Mount the embedded Checkout page in-page (Stripe-hosted iframe).
      const stripe = Stripe(PUBLISHABLE_KEY);
      const checkout = await stripe.initEmbeddedCheckout({
        clientSecret: client_secret,
      });
      checkout.mount("#checkout-form");
      // 3. On completion, show the confirmation in place. The server is set to
      //    `redirect_on_completion="never"` (and passes no `return_url`), so the
      //    buyer stays on /billing instead of being redirected anywhere.
      checkout.on("onComplete", () => setSubmitted(true));
    } catch (exc) {
      setError(
        exc instanceof Error ? exc.message : "Checkout couldn't start; please try again.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      <section className="card bg-base-200 shadow">
        <div className="card-body gap-4">
          <h1 className="card-title text-3xl">Pro</h1>
          <p className="text-5xl font-bold">
            $5<span className="text-xl opacity-70">/mo</span>
          </p>
          <ul className="list-disc gap-2 flex flex-col pl-4">
            {FEATURES.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>

          {user === undefined ? (
            <span className="loading loading-spinner" />
          ) : user === null ? (
            <button className="btn btn-primary" onClick={signIn}>
              Sign in to subscribe
            </button>
          ) : user.tier === "paid" ? (
            <p>
              <span className="badge badge-accent badge-lg">You&apos;re on Pro</span>
            </p>
          ) : (
            <>
              <button
                className="btn btn-primary"
                onClick={() => void start()}
                disabled={loading}
              >
                {loading ? "Loading checkout…" : "Subscribe to Pro"}
              </button>
              {submitted && (
                <p className="text-sm opacity-80">
                  Thanks — your subscription is being set up.{" "}
                  <strong>It activates once your payment is confirmed</strong> (the
                  plan-upgrade webhook is the next step for this project).
                </p>
              )}
              {/* Stripe.js mounts the embedded Checkout form here. */}
              <div id="checkout-form" className="mt-4" />
            </>
          )}
          {error && <p className="text-error text-sm mt-2">{error}</p>}
        </div>
      </section>
    </div>
  );
}