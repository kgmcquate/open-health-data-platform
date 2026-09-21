export default function Footer() {
  return (
    <footer className="footer sm:footer-horizontal bg-base-200 text-base-content p-8 mt-16 border-t border-base-300">
      <aside>
        <p className="font-bold">Open Health Data Platform</p>
        <p className="max-w-md text-sm opacity-80">
          Population-level figures from public data. Not clinical decision
          support — always consult a qualified professional for medical
          questions.
        </p>
      </aside>
      <nav>
        <h6 className="footer-title">Platform</h6>
        <a className="link link-hover" href="/data-sources">Data sources</a>
        <a className="link link-hover" href="/dashboards">Dashboards</a>
        <a className="link link-hover" href="/chat">Chat</a>
      </nav>
      <nav>
        <h6 className="footer-title">Built in the open</h6>
        <a className="link link-hover" href="http://localhost:8585" target="_blank" rel="noreferrer">
          OpenMetadata catalog
        </a>
        <a className="link link-hover" href="http://localhost:3000" target="_blank" rel="noreferrer">
          Dagster pipelines
        </a>
      </nav>
    </footer>
  );
}
