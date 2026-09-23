import type { ReactNode } from "react";
import { useParams } from "react-router-dom";
import {
  fetchDashboards,
  fetchLiterature,
  fetchTopic,
  type CatalogAsset,
  type Dashboard,
  type LiteratureItem,
  type TopicDetail as TopicDetailData,
} from "../lib/api";
import { useFetch } from "../lib/useFetch";
import { DashboardCard } from "./Dashboards";

function AssetList({ assets }: { assets: CatalogAsset[] }) {
  return (
    <ul className="space-y-1">
      {assets.map((asset) => (
        <li key={asset.id}>
          <a href={asset.catalog_url} target="_blank" rel="noreferrer" className="link link-hover">
            {asset.name}
          </a>
          {asset.description && (
            <span className="opacity-60"> — {asset.description}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function LiteratureList({ items }: { items: LiteratureItem[] }) {
  return (
    <div className="space-y-4">
      {items.map((item) => (
        <article key={item.id} className="card bg-base-200 shadow-sm">
          <div className="card-body">
            <div className="flex items-center gap-2 flex-wrap">
              {item.trending && <span className="badge badge-accent">🔥 trending</span>}
            </div>
            <h3 className="card-title text-base mt-1">
              <a href={item.url} target="_blank" rel="noreferrer" className="link link-hover">
                {item.title}
              </a>
            </h3>
            <p className="text-sm opacity-60">
              {item.authors}
              {item.venue && ` · ${item.venue}`}
              {item.year && ` · ${item.year}`}
              {item.doi && (
                <>
                  {" · "}
                  <a
                    href={`https://doi.org/${item.doi}`}
                    target="_blank"
                    rel="noreferrer"
                    className="link"
                  >
                    doi:{item.doi}
                  </a>
                </>
              )}
            </p>
            {item.summary && <p className="opacity-80 mt-1">{item.summary}</p>}
          </div>
        </article>
      ))}
    </div>
  );
}

function Section({
  title,
  loading,
  error,
  empty,
  children,
}: {
  title: string;
  loading: boolean;
  error: string | null;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <section className="mt-10">
      <h2 className="text-2xl font-bold mb-4">{title}</h2>
      {loading && <span className="loading loading-spinner" />}
      {error && <div className="alert alert-error">{error}</div>}
      {!loading && !error && empty && (
        <p className="opacity-60 text-sm">Nothing here yet.</p>
      )}
      {!loading && !error && !empty && children}
    </section>
  );
}

export default function TopicDetail() {
  const { name = "" } = useParams<{ name: string }>();

  const topic = useFetch<TopicDetailData>(() => fetchTopic(name), [name]);
  const dashboards = useFetch<Dashboard[]>(() => fetchDashboards(name), [name]);
  const literature = useFetch<LiteratureItem[]>(() => fetchLiterature(name), [name]);

  if (topic.loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-24 text-center">
        <span className="loading loading-spinner loading-lg" />
      </div>
    );
  }

  if (topic.error || !topic.data) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-10">
        <div className="alert alert-error">
          {topic.error ?? "That topic couldn't be found."}
        </div>
      </div>
    );
  }

  const { topic: domain, metrics, assets, sources } = topic.data;

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-4xl font-bold mb-2">{domain.name}</h1>
          <p className="opacity-70 max-w-2xl">{domain.description}</p>
        </div>
        {domain.catalog_url && (
          <a href={domain.catalog_url} target="_blank" rel="noreferrer" className="btn btn-sm btn-secondary">
            Open in catalog ↗
          </a>
        )}
      </div>

      {sources.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mt-4">
          <span className="text-sm opacity-60">Sources:</span>
          {sources.map((source) => (
            <a
              key={source.id}
              href={source.catalog_url}
              target="_blank"
              rel="noreferrer"
              className="badge badge-outline"
            >
              {source.name}
            </a>
          ))}
        </div>
      )}

      <Section title="Metrics" loading={false} error={null} empty={metrics.length === 0}>
        <AssetList assets={metrics} />
      </Section>

      <Section title="Data assets" loading={false} error={null} empty={assets.length === 0}>
        <AssetList assets={assets} />
      </Section>

      <Section
        title="Dashboards"
        loading={dashboards.loading}
        error={dashboards.error}
        empty={(dashboards.data ?? []).length === 0}
      >
        <div className="space-y-6">
          {dashboards.data?.map((dashboard) => (
            <DashboardCard key={dashboard.id} dashboard={dashboard} />
          ))}
        </div>
      </Section>

      <Section
        title="Literature"
        loading={literature.loading}
        error={literature.error}
        empty={(literature.data ?? []).length === 0}
      >
        <LiteratureList items={literature.data ?? []} />
      </Section>
    </div>
  );
}
