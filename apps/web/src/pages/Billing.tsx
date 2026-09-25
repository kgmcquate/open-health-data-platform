import { useEffect, useRef, useState } from "react";
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
    onComplete?: () => void;
  }) => Promise<StripeCheckout>;
};
type StripeCheckout = {
  mount: (selector: string) => void;
  destroy: () => void;
};

/** The Pro plans page (`hub_api.billing`): a $5/mo Stripe subscription sold via
 * Stripe's **embedded Checkout** page. The server hands back a `client_secret`;
 * `initEmbeddedCheckout` mounts the Stripe-hosted Checkout page into the modal
 * below and the purchase completes on this origin (the server disables the
 * post-payment redirect, so the `onComplete` option is what flips the UI to the
 * "Thanks" state).
 *
 * The numbers in `FEATURES` are the paid-tier caps the server enforces — the
 * public plan copy, not a quota source of truth. */
export default function Billing() {
  const { user, signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  // The embedded Checkout page lives inside a modal. These book-keep the modal
  // and the mounted checkout so we create one session per open, mount it once,
  // and tear the iframe down when the modal is dismissed.
  const modalRef = useRef<HTMLDialogElement>(null);
  const checkoutRef = useRef<StripeCheckout | null>(null);
  const isOpenRef = useRef(false);

  // Tear down any lingering checkout iframe if the page unmounts first.
  useEffect(() => () => checkoutRef.current?.destroy(), []);

  const handleClose = () => {
    isOpenRef.current = false;
    checkoutRef.current?.destroy();
    checkoutRef.current = null;
    setLoading(false);
    setSubmitted(false);
    setError(null);
  };

  const openCheckout = () => {
    setError(null);
    setSubmitted(false);
    isOpenRef.current = true;
    modalRef.current?.showModal();
    void start();
  };

  const start = async () => {
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
      // 2. Mount the embedded Checkout page into the modal. `onComplete` is an
      //    option to `initEmbeddedCheckout` (not an `.on()` method on the
      //    checkout object). The server disables the redirect, so `onComplete`
      //    is what flips the UI to the "Thanks" state in place.
      const stripe = Stripe(PUBLISHABLE_KEY);
      const checkout = await stripe.initEmbeddedCheckout({
        clientSecret: client_secret,
        onComplete: () => setSubmitted(true),
      });
      // If the modal was dismissed while we were creating the session, don't
      // mount into the now-hidden container.
      if (!isOpenRef.current) {
        checkout.destroy();
        return;
      }
      checkoutRef.current = checkout;
      checkout.mount("#checkout-form");
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
            <button className="btn btn-primary" onClick={openCheckout}>
              Subscribe to Pro
            </button>
          )}
        </div>
      </section>

      {/* Embedded Checkout, mounted in a modal. The Stripe page stays in-page —
          the server disables the post-payment redirect, so the buyer never
          leaves /billing. */}
      <dialog ref={modalRef} className="modal" onClose={handleClose}>
        {/* Wide and tall: the embedded Checkout page is Stripe's own
            responsive layout, not ours — a narrow container forces it into a
            single stacked column (order summary above payment form) that
            runs long. Past ~900px it lays those two out side by side
            instead, which is most of the fix; max-h/overflow-y-auto is the
            fallback for whatever's still too tall for a short viewport. */}
        <div className="modal-box w-11/12 max-w-4xl max-h-[90vh] overflow-y-auto">
          <div className="flex items-start justify-between gap-4 mb-4">
            <div>
              <h3 className="text-lg font-bold">Subscribe to Pro</h3>
            </div>
            <form method="dialog">
              <button
                className="btn btn-sm btn-circle btn-ghost"
                aria-label="Close"
              >
                ✕
              </button>
            </form>
          </div>

          {submitted ? (
            <p className="text-sm">
              Thanks — your subscription is being set up.{" "}
              <strong>It activates once your payment is confirmed</strong>
            </p>
          ) : (
            <div className="relative min-h-[500px]">
              {/* Always in the DOM while the modal is open so Stripe's
                  mount("#checkout-form") has something to attach to — it's
                  called mid-`start()`, before `loading` flips back to false. */}
              <div id="checkout-form" className="w-full min-h-[500px]" />
              {loading && (
                <div className="absolute inset-0 flex justify-center items-center py-10">
                  <span className="loading loading-spinner loading-lg" />
                </div>
              )}
            </div>
          )}
          {error && <p className="text-error text-sm mt-2">{error}</p>}
        </div>
        <form method="dialog" className="modal-backdrop">
          <button>close</button>
        </form>
      </dialog>
    </div>
  );
}