export default function Footer() {
  return (
    <footer className="footer sm:footer-horizontal bg-base-200 text-base-content p-8 mt-16 border-t border-base-300">
      <aside>
        <p className="font-bold">Open Health Data Platform</p>
        <p className="max-w-md text-sm opacity-80">
          Population-level figures from public data.
          Content is for information only, not medical advice.
        </p>
      </aside>
      <nav>
        <h6 className="footer-title">Legal</h6>
        <a className="link link-hover" href="/privacy">Privacy policy</a>
        <a className="link link-hover" href="/terms">Terms of service</a>
      </nav>
      <nav>
        <h6 className="footer-title">Support</h6>
        <a className="link link-hover" href="/support">Report a bug, request a feature</a>
        <a className="link link-hover" href="mailto:support@open-health-data-platform.org">
          support@open-health-data-platform.org
        </a>
      </nav>
      <nav>
        <h6 className="footer-title">Built in the open</h6>
        <a className="link link-hover" href="https://catalog.open-health-data-platform.org" target="_blank" rel="noreferrer">
          OpenMetadata catalog
        </a>
        <a className="link link-hover" href="https://dagster.open-health-data-platform.org" target="_blank" rel="noreferrer">
          Dagster pipelines
        </a>
      </nav>
    </footer>
  );
}
