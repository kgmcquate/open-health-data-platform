import { useEffect, useRef } from "react";
import vegaEmbed from "vega-embed";
import { fetchDashboards, type CuratedDashboard } from "../lib/api";
import { useFetch } from "../lib/useFetch";

function DashboardCard({ dashboard }: { dashboard: CuratedDashboard }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    // Curated specs are trusted (written by us, never by the agent — the
    // agent's own specs are validated server-side before rows are bound).
    void vegaEmbed(ref.current, dashboard.vega as never, {
      actions: false,
      renderer: "canvas",
    });
  }, [dashboard.vega]);

  return (
    <div className="card bg-base-200 shadow-sm">
      <div className="card-body">
        <div className="flex items-center gap-2">
          <h2 className="card-title">{dashboard.title}</h2>
          {dashboard.featured && <span className="badge badge-accent">featured</span>}
        </div>
        <p className="opacity-80">{dashboard.description}</p>
        <div ref={ref} className="mt-4 overflow-x-auto" />
        <details className="collapse collapse-arrow bg-base-300 mt-2">
          <summary className="collapse-title text-sm font-medium">
            The query behind this dashboard
          </summary>
          <div className="collapse-content">
            <pre className="text-xs overflow-x-auto p-2">
              {JSON.stringify(dashboard.cube_query, null, 2)}
            </pre>
          </div>
        </details>
      </div>
    </div>
  );
}

export default function Dashboards() {
  const { data, loading, error } = useFetch<CuratedDashboard[]>(fetchDashboards);

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Dashboards</h1>
      <p className="opacity-70 mb-8 max-w-2xl">
        Curated visualizations over the semantic layer. Every dashboard shows
        the exact metric query behind it — and the chatbot can reproduce any of
        them on demand.
      </p>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {data && data.length === 0 && (
        <div className="alert">
          No curated dashboards yet — ask the chatbot to draw something and it
          may end up here.
        </div>
      )}

      <div className="space-y-6">
        {data?.map((dashboard) => (
          <DashboardCard key={dashboard.id} dashboard={dashboard} />
        ))}
      </div>
    </div>
  );
}
