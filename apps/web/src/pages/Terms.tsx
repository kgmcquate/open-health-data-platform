const LAST_UPDATED = "September 26, 2026";

export default function Terms() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Terms of service</h1>
      <p className="opacity-60 text-sm mb-8">Last updated {LAST_UPDATED}</p>

      <p className="opacity-80 mb-6">
        These terms cover your use of Open Health Data Platform ("OHDP",
        "we"), including its dashboards, topics, search, and assistant. OHDP
        is operated by Kevin McQuate, an individual based in Maryland, United
        States. By
        using the site you agree to them. How we handle personal data is
        described separately in our{" "}
        <a className="link" href="/privacy">
          privacy policy
        </a>
        .
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Not medical advice</h2>
      <p className="opacity-80">
        OHDP publishes population-level statistics drawn from public sources.
        Nothing on the site — including answers from the assistant — is
        medical advice, a diagnosis, or a substitute for a qualified health
        professional. Don't use it to make decisions about an individual's
        care.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Accuracy of data and answers</h2>
      <p className="opacity-80">
        Figures come from third-party public datasets, which can be delayed,
        revised, or wrong at the source, and our processing of them can
        introduce errors of its own. The assistant is a language model: it
        can misread a question, pick the wrong dataset, or state something
        confidently that isn't true. Check anything important against the
        original source, which is linked from the catalog.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Accounts</h2>
      <p className="opacity-80">
        Browsing dashboards and topics doesn't require an account. The
        assistant and some other features require signing in with Google (or
        another configured identity provider). You're responsible for
        activity under your account, and each account is for one person.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Acceptable use</h2>
      <p className="opacity-80 mb-2">When using OHDP, don't:</p>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>Break the law, or use the site to harm others.</li>
        <li>
          Try to get around usage quotas, for example by creating multiple
          accounts or sharing one.
        </li>
        <li>
          Probe, overload, or disrupt the platform, or access parts of it
          you're not authorized to use.
        </li>
        <li>
          Use the assistant to generate spam, or file junk, abusive, or
          misleading issue reports.
        </li>
        <li>
          Submit other people's personal or health information in chats or
          reports.
        </li>
      </ul>
      <p className="opacity-80 mt-4">
        We may limit, suspend, or close accounts that break these rules.
      </p>

      <h2 id="plus-subscription" className="text-2xl font-bold mt-8 mb-2">
        Plus subscription
      </h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          Plus is a paid subscription that raises your usage limits, billed
          monthly at the price shown on the{" "}
          <a className="link" href="/billing">
            billing page
          </a>{" "}
          when you subscribe (currently $5/month, plus any applicable tax).
          Payments are processed by Stripe; we never see or store your full
          card number.
        </li>
        <li>
          <span className="font-semibold">
            Your subscription renews automatically
          </span>{" "}
          each month, and your payment method is charged at the start of each
          billing period, until you cancel.
        </li>
        <li>
          If the price changes, we'll email you at least 30 days before the
          new price applies to your subscription, and you can cancel before
          then.
        </li>
      </ul>

      <h2 id="cancellation" className="text-2xl font-bold mt-8 mb-2">
        Cancellation
      </h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          You can cancel at any time, online, by choosing "Manage
          subscription" on the{" "}
          <a className="link" href="/billing">
            billing page
          </a>
          . You don't need to contact us, and there's no cancellation fee. If
          you'd rather, email{" "}
          <a className="link" href="mailto:support@open-health-data-platform.org">
            support@open-health-data-platform.org
          </a>{" "}
          and we'll cancel it for you.
        </li>
        <li>
          Cancellation stops the next renewal. You keep Plus limits until the
          end of the billing period you've already paid for, then your account
          returns to the free tier. Your account and saved content aren't
          deleted when you cancel.
        </li>
        <li>
          We may cancel a subscription if the account is suspended for
          breaking these terms, or if we discontinue Plus. If we discontinue
          Plus for reasons other than a breach of these terms, we'll refund
          the unused portion of the current billing period.
        </li>
      </ul>

      <h2 id="refunds" className="text-2xl font-bold mt-8 mb-2">
        Refunds
      </h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          Payments are generally non-refundable, and we don't give prorated
          refunds for a partial month when you cancel mid-period.
        </li>
        <li>
          We will refund a charge in full if you were charged in error — for
          example, a duplicate charge, a charge after you'd cancelled, or a
          renewal you didn't authorize — or if Plus was substantially
          unavailable for most of a billing period. Email{" "}
          <a className="link" href="mailto:support@open-health-data-platform.org">
            support@open-health-data-platform.org
          </a>{" "}
          within 30 days of the charge. Approved refunds go back to the
          original payment method, usually within 5–10 business days.
        </li>
        <li>
          Nothing in this section limits any refund or withdrawal right the
          law where you live gives you and doesn't allow to be waived.
        </li>
      </ul>

      <h2 className="text-2xl font-bold mt-8 mb-2">Your content</h2>
      <p className="opacity-80">
        You keep ownership of what you type into the site. You give us
        permission to process it as needed to run the service — for example,
        sending your questions to the language-model backend, and publishing
        issue reports you ask us to file in our public GitHub repository.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Our software and data</h2>
      <p className="opacity-80">
        OHDP's source code is published under the{" "}
        <a
          className="link"
          href="https://polyformproject.org/licenses/noncommercial/1.0.0"
          target="_blank"
          rel="noreferrer"
        >
          PolyForm Noncommercial License 1.0.0
        </a>
        . The underlying public datasets stay subject to their original
        publishers' terms.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Availability</h2>
      <p className="opacity-80">
        We're working to keep OHDP running, but we don't guarantee it will
        always be available or error-free, and we may change or remove
        features over time.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Disclaimers and liability</h2>
      <p className="opacity-80">
        OHDP is provided "as is", without warranties of any kind. To the
        extent the law allows, we aren't liable for indirect or consequential
        losses arising from your use of the site, and our total liability to
        you is limited to the amount you paid us in the 12 months before the
        claim.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Governing law and disputes</h2>
      <p className="opacity-80">
        These terms are governed by the laws of the State of Maryland, United
        States, without regard to its conflict-of-laws rules. Any dispute
        arising from these terms or your use of OHDP will be resolved in the
        state or federal courts located in Maryland, and you and we consent to
        their jurisdiction. If you're a consumer, this doesn't take away any
        protection the law of the place you live gives you that can't be
        waived by contract, including the right to bring a claim in your local
        courts where that law allows it. Before filing a claim, please email
        us first — most problems can be sorted out that way.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Changes to these terms</h2>
      <p className="opacity-80">
        If these terms change, we'll update the date at the top of this page.
        For material changes that affect paid subscriptions, we'll also let
        subscribers know by email. Continuing to use the site after a change
        means you accept the updated terms.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Contact</h2>
      <p className="opacity-80">
        Questions about these terms, billing, or your subscription can be
        sent to{" "}
        <a className="link" href="mailto:support@open-health-data-platform.org">
          support@open-health-data-platform.org
        </a>
        . For questions about your personal data, use{" "}
        <a className="link" href="mailto:privacy@open-health-data-platform.org">
          privacy@open-health-data-platform.org
        </a>
        .
      </p>
    </div>
  );
}
