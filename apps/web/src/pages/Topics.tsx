import { Link } from "react-router-dom";
import { fetchTopics, type Topic } from "../lib/api";
import { useFetch } from "../lib/useFetch";

export default function Topics() {
  const { data, loading, error } = useFetch<Topic[]>(fetchTopics);

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Topics</h1>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {data && data.length === 0 && <div className="alert">No topics yet.</div>}

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {data?.map((topic) => (
          <Link
            key={topic.id}
            to={`/topics/${encodeURIComponent(topic.name)}`}
            className="card bg-base-200 shadow-sm hover:shadow-md transition-shadow"
          >
            <div className="card-body">
              <h2 className="card-title">{topic.name}</h2>
              <p className="opacity-80 line-clamp-3">{topic.description}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
