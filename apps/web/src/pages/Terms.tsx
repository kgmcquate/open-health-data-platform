const LAST_UPDATED = "September 25, 2026";

export default function Terms() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Terms of service</h1>
      <p className="opacity-60 text-sm mb-8">Last updated {LAST_UPDATED}</p>

      <p className="opacity-80 mb-6">
        These terms cover your use of Open Health Data Platform ("OHDP",
        "we"), including its dashboards, topics, search, and assistant. By
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

      <h2 className="text-2xl font-bold mt-8 mb-2">Plus subscription</h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          Plus is a paid monthly subscription that raises your usage limits,
          at the price shown on the{" "}
          <a className="link" href="/billing">
            billing page
          </a>{" "}
          when you subscribe. Payments are processed by Stripe; we never see
          or store your full card number.
        </li>
        <li>
          Your subscription renews automatically each month until you cancel.
          You can cancel at any time from the billing page. Cancellation
          takes effect at the end of the current billing period, and you keep
          Plus limits until then.
        </li>
        <li>
          Payments are non-refundable, except where the law requires
          otherwise. If you were charged in error, contact us and we'll sort
          it out.
        </li>
        <li>
          If the price changes, we'll tell you before it applies to your
          subscription, and you can cancel before then.
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

      <h2 className="text-2xl font-bold mt-8 mb-2">Changes to these terms</h2>
      <p className="opacity-80">
        If these terms change, we'll update the date at the top of this page.
        For material changes that affect paid subscriptions, we'll also let
        subscribers know by email. Continuing to use the site after a change
        means you accept the updated terms.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Contact</h2>
      <p className="opacity-80">
        Questions about these terms can be sent to{" "}
        <a className="link" href="mailto:privacy@open-health-data-platform.org">
          privacy@open-health-data-platform.org
        </a>
        .
      </p>
    </div>
  );
}
