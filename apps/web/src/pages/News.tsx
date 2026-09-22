import { fetchNews, type NewsItem } from "../lib/api";
import { useFetch } from "../lib/useFetch";

const KIND_BADGE: Record<string, string> = {
  study: "badge-secondary",
  visualization: "badge-accent",
  platform: "badge-primary",
};

export default function News() {
  const { data, loading, error } = useFetch<NewsItem[]>(fetchNews);

  return (
    <div className="max-w-4xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">News</h1>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {data && data.length === 0 && (
        <div className="alert">Nothing here yet — check back soon.</div>
      )}

      <div className="space-y-4">
        {data?.map((item) => (
          <article key={item.id} className="card bg-base-200 shadow-sm">
            <div className="card-body">
              <div className="flex items-center gap-2">
                <span className={`badge ${KIND_BADGE[item.kind] ?? "badge-ghost"}`}>
                  {item.kind}
                </span>
                <time className="text-xs opacity-60">
                  {new Date(item.published_at).toLocaleDateString()}
                </time>
              </div>
              <h2 className="card-title mt-1">{item.title}</h2>
              <p className="opacity-80">{item.summary}</p>
              <div className="card-actions justify-end">
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="btn btn-sm btn-primary"
                >
                  Read more ↗
                </a>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
