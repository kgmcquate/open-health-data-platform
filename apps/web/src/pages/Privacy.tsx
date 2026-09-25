const LAST_UPDATED = "September 23, 2026";

export default function Privacy() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Privacy policy</h1>
      <p className="opacity-60 text-sm mb-8">Last updated {LAST_UPDATED}</p>

      <p className="opacity-80 mb-6">
        Open Health Data Platform ("OHDP", "we") publishes population-level
        public health data and an assistant for exploring it. This page
        describes the personal data the platform itself collects when you
        create an account, chat, or file feedback — not the health datasets
        shown in dashboards, which are aggregate, publicly sourced statistics
        and never about you individually.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">What we collect</h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          <span className="font-semibold">Account info.</span> Signing in
          uses Google (or another configured OIDC provider). We receive your
          email address and name, and store them along with your usage tier
          in our database.
        </li>
        <li>
          <span className="font-semibold">Session cookie.</span> A signed,
          HttpOnly cookie keeps you signed in for up to 14 days. It carries
          your email, name, and tier — nothing else — and isn't used for
          tracking or advertising.
        </li>
        <li>
          <span className="font-semibold">Chat messages.</span> Questions you
          ask the assistant are sent to the language-model backend the
          platform is configured to use, and logged on our servers together
          with your account email, so we can enforce usage quotas and
          diagnose problems.
        </li>
        <li>
          <span className="font-semibold">Issue reports.</span> If you submit
          a report through the Support page, or ask the assistant to file
          feedback or a bug report, that content is filed as a public issue in
          our GitHub repository, along with your account email. We strip
          anything that looks like a secret or credential first, but please
          don't paste sensitive personal information into a report you ask us
          to file publicly.
        </li>
      </ul>
      <p className="opacity-80 mt-4">
        We do not use advertising cookies, analytics trackers, or any other
        third-party tracking scripts on this site.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Why we collect it</h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>To authenticate you and keep you signed in.</li>
        <li>To enforce per-tier daily usage quotas.</li>
        <li>To answer the questions you ask the assistant.</li>
        <li>
          To file issues or feedback you explicitly ask us to submit on your
          behalf.
        </li>
        <li>To maintain and improve the platform, and investigate abuse.</li>
      </ul>

      <h2 className="text-2xl font-bold mt-8 mb-2">Who we share it with</h2>
      <ul className="list-disc pl-6 space-y-2 opacity-80">
        <li>
          <span className="font-semibold">Our OIDC provider (e.g. Google)</span>
          , to verify your identity when you sign in.
        </li>
        <li>
          <span className="font-semibold">
            The language-model backend(s) this deployment is configured with
          </span>
          , to generate answers to your chat messages.
        </li>
        <li>
          <span className="font-semibold">GitHub</span>, only for the specific
          content of an issue you ask the assistant to file — it becomes
          public in our repository.
        </li>
      </ul>
      <p className="opacity-80 mt-4">
        We do not sell personal data, and do not share it with anyone else.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">How long we keep it</h2>
      <p className="opacity-80">
        Your account record and chat logs are kept until you ask us to delete
        them. The session cookie itself expires automatically after 14 days.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Your choices</h2>
      <p className="opacity-80">
        To request a copy of your data or ask us to delete your account and
        associated chat history, email{" "}
        <a className="link" href="mailto:privacy@open-health-data-platform.org">
          privacy@open-health-data-platform.org
        </a>
        . You can also stop using the assistant or issue-filing features at
        any time without affecting your ability to browse public dashboards
        and topics, which don't require signing in.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Children</h2>
      <p className="opacity-80">
        OHDP is not directed at children, and we do not knowingly collect
        personal data from anyone under 13.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Changes to this policy</h2>
      <p className="opacity-80">
        If this policy changes, we'll update the date at the top of this
        page.
      </p>

      <h2 className="text-2xl font-bold mt-8 mb-2">Contact</h2>
      <p className="opacity-80">
        Questions about this policy or your data can be sent to{" "}
        <a className="link" href="mailto:privacy@open-health-data-platform.org">
          privacy@open-health-data-platform.org
        </a>
        .
      </p>
    </div>
  );
}
