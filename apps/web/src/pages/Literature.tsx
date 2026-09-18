import { fetchLiterature, type LiteratureItem } from "../lib/api";
import { useFetch } from "../lib/useFetch";

export default function Literature() {
  const { data, loading, error } = useFetch<LiteratureItem[]>(fetchLiterature);

  return (
    <div className="max-w-4xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Literature</h1>
      <p className="opacity-70 mb-8 max-w-2xl">
        Popular, trending, and curated studies related to the data on this
        platform. Summaries are ours; the science is theirs.
      </p>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {data && data.length === 0 && (
        <div className="alert">No curated literature yet.</div>
      )}

      <div className="space-y-4">
        {data?.map((item) => (
          <article key={item.id} className="card bg-base-200 shadow-sm">
            <div className="card-body">
              <div className="flex items-center gap-2 flex-wrap">
                {item.trending && (
                  <span className="badge badge-accent">🔥 trending</span>
                )}
                {item.tags.map((tag) => (
                  <span key={tag} className="badge badge-outline badge-sm">
                    {tag}
                  </span>
                ))}
              </div>
              <h2 className="card-title mt-1">
                <a href={item.url} target="_blank" rel="noreferrer" className="link link-hover">
                  {item.title}
                </a>
              </h2>
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
    </div>
  );
}
