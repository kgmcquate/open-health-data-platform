import { fetchDataSources, type DataSource } from "../lib/api";
import { useFetch } from "../lib/useFetch";

export default function DataSources() {
  const { data, loading, error } = useFetch<DataSource[]>(fetchDataSources);

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Data Sources</h1>
      <p className="opacity-70 mb-8 max-w-2xl">
        Everything on this platform is built over public datasets. Explore what
        we ingest, and jump into the live catalog to see every table, its
        lineage, and its freshness.
      </p>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {data?.map((source) => (
          <div key={source.id} className="card bg-base-200 shadow-sm hover:shadow-md transition-shadow">
            <div className="card-body">
              <h2 className="card-title">{source.name}</h2>
              <p className="opacity-80">{source.description}</p>
              <div className="card-actions justify-end mt-2">
                {source.catalog_url && (
                  <a
                    href={source.catalog_url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn btn-sm btn-secondary"
                  >
                    Open in catalog ↗
                  </a>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
