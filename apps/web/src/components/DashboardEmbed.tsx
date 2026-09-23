import { useEffect, useRef, useState } from "react";

/** A rendered dashboard page, isolated in a sandboxed iframe.
 *
 * The page is a full HTML document (`ohdp_agent.dashboard.render_html`) — Vega
 * loaded from a CDN, the chart's own data table and query in a `<details>`, and
 * a script that posts its rendered height so this frame can size itself (there
 * is no same-origin access into a sandboxed iframe to measure it directly).
 * `sandbox` omits allow-same-origin for exactly that isolation, but keeps
 * allow-scripts (Vega must run) and allow-popups (the chart's own "..." export
 * menu opens a new tab).
 *
 * Two callers, two ways in. The chat has the document in hand as a tool result
 * and passes `html`; the Dashboards page has a URL for the stored render and
 * passes `src`, which keeps the document out of the page's own JSON payload and
 * lets the browser cache it. The height protocol is identical either way, which
 * is why this lives here rather than being written twice.
 */
export function DashboardEmbed({
  html,
  src,
  title = "Dashboard",
}: {
  html?: string;
  src?: string;
  title?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(320);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return;
      const data = e.data as { type?: string; height?: number } | undefined;
      if (data?.type === "iframe:height" && typeof data.height === "number") {
        setHeight(Math.max(160, Math.ceil(data.height)));
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <iframe
      ref={iframeRef}
      srcDoc={html}
      src={src}
      sandbox="allow-scripts allow-popups"
      title={title}
      className="w-full rounded-box border border-base-300 bg-base-100"
      style={{ height }}
    />
  );
}
