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

// --- Stripe.js (the `dahlia` build, loaded from index.html) ---------------
// The embedded Checkout form is rendered by Stripe's Checkout Form SDK:
//   Stripe(publishableKey, { betas }) -> initCheckoutFormSdk({clientSecret, appearance})
// Typed loosely here — it is an alpha API reached through a global script, not
// a package we can import. The publishable key is browser-accessible, so it
// MUST come from a VITE_-prefixed env var (baked in at `vite build` time).
const PUBLISHABLE_KEY = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY as
  | string
  | undefined;

type StripeGlobal = {
  Stripe?: (
    publishableKey: string,
    options: { betas: string[] },
  ) => StripeSdk;
};
type StripeSdk = {
  initCheckoutFormSdk: (options: {
    clientSecret: string;
    appearance: Record<string, unknown>;
  }) => StripeCheckout;
};
type StripeCheckout = {
  createForm: (options: { layout: "expanded" }) => StripeCheckoutForm;
  loadActions: () => Promise<StripeLoadActionsResult>;
};
type StripeCheckoutForm = {
  mount: (selector: string) => void;
  on: (event: string, handler: (event: unknown) => void) => void;
};
type StripeLoadActionsResult = {
  type: "success" | string;
  actions?: {
    confirm: (options: { formConfirmEvent: unknown }) => Promise<void>;
  };
};

/** The Checkout Form SDK appearance — the configured look-and-feel for the
 * Stripe-hosted iframe. Values set in the Checkout Studio UI. */
const APPEARANCE: Record<string, unknown> = {
  theme: "stripe",
  labels: "auto",
  inputs: "spaced",
  variables: {
    borderRadius: "4px",
    colorBackground: "#ffffff",
    colorDanger: "#df1b41",
    colorPrimary: "#0570de",
    colorSuccess: "#00c853",
    colorText: "#30313d",
    fontFamily: "default",
    fontSizeBase: "16px",
    spacingUnit: "4px",
  },
};

/** The Pro plans page (`hub_api.billing`): a $5/mo Stripe subscription sold via
 * Stripe's **embedded** Checkout form. The server hands back a `client_secret`
 * (never a redirect URL); `initCheckoutFormSdk` mounts the Stripe-hosted form
 * into `#checkout-form` below and confirms the payment in-page, on this origin.
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
      // 2. Initialize the Checkout Form SDK with the client secret + appearance.
      const stripe = Stripe(PUBLISHABLE_KEY, { betas: ["custom_checkout_payment_form_1"] });
      const checkout = stripe.initCheckoutFormSdk({
        clientSecret: client_secret,
        appearance: APPEARANCE,
      });
      // 3. Create, mount, and wire the confirm event.
      const form = checkout.createForm({ layout: "expanded" });
      form.mount("#checkout-form");
      const loadActionsResult = await checkout.loadActions();
      if (loadActionsResult.type === "success" && loadActionsResult.actions) {
        form.on("confirm", async (event) => {
          try {
            await loadActionsResult.actions!.confirm({ formConfirmEvent: event });
            setSubmitted(true);
          } catch (exc) {
            console.error("Payment confirmation error:", exc);
          }
        });
      } else {
        setError("Checkout couldn't start; please try again.");
      }
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