import { Link, useSearchParams } from "react-router-dom";
import { fetchSearch, type SearchResults } from "../lib/api";
import { useFetch } from "../lib/useFetch";

/** Per-section cap for this page — more than the header dropdown's default,
 * and the server's own ceiling is what actually bounds it. */
const PAGE_LIMIT = 50;

/** The kinds of thing a search can turn up, in the order the page lists them.
 *
 * `id` is what goes in the URL's `types=`, so these strings are part of a
 * shareable link and should not be renamed casually. `count` reads the section
 * out of one `SearchResults`, which is also how the rail shows how many of
 * each kind matched — the filter is applied over what the server already sent
 * (up to `PAGE_LIMIT` per section), not by asking again.
 *
 * Tables and metrics arrive in one `assets` list and are split here: they are
 * different enough things to filter separately, which is the distinction the
 * `entity_type` badge was already making inside the one section. */
const FACETS = [
  { id: "topic", label: "Topics", count: (r: SearchResults) => r.topics.length },
  { id: "dashboard", label: "Dashboards", count: (r: SearchResults) => r.dashboards.length },
  { id: "dataset", label: "Datasets", count: (r: SearchResults) => datasets(r).length },
  { id: "metric", label: "Metrics", count: (r: SearchResults) => metrics(r).length },
  { id: "paper", label: "Literature", count: (r: SearchResults) => r.literature.length },
] as const;

const datasets = (results: SearchResults) =>
  results.assets.filter((asset) => asset.entity_type !== "metric");
const metrics = (results: SearchResults) =>
  results.assets.filter((asset) => asset.entity_type === "metric");

/** The left rail. Checkboxes rather than one-at-a-time tabs: "dashboards and
 * metrics, not papers" is a normal thing to want, and nothing selected means
 * everything — the same state as all five selected, so the empty URL is the
 * unfiltered one. */
function TypeFilter({
  results,
  selected,
  onToggle,
  onClear,
}: {
  results: SearchResults | null;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <aside className="lg:w-56 shrink-0">
      <div className="lg:sticky lg:top-24">
        <div className="flex items-baseline justify-between mb-2">
          <h2 className="font-semibold">Type</h2>
          {selected.size > 0 && (
            <button type="button" className="btn btn-ghost btn-xs" onClick={onClear}>
              Clear
            </button>
          )}
        </div>
        <ul className="flex flex-wrap lg:flex-col gap-x-2 gap-y-1">
          {FACETS.map((facet) => {
            // No count at all until results are in, rather than a `0` that
            // would read as "none of these" while the fetch is still running.
            const count = results ? facet.count(results) : null;
            return (
              <li key={facet.id}>
                <label className="label cursor-pointer justify-start gap-2 py-1">
                  <input
                    type="checkbox"
                    className="checkbox checkbox-sm checkbox-primary"
                    checked={selected.has(facet.id)}
                    onChange={() => onToggle(facet.id)}
                  />
                  <span className={count === 0 ? "opacity-50" : ""}>{facet.label}</span>
                  {count !== null && <span className="badge badge-ghost badge-sm">{count}</span>}
                </label>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}

function Section({
  title,
  caption,
  children,
  count,
}: {
  title: string;
  caption: string;
  count: number;
  children: React.ReactNode;
}) {
  if (count === 0) return null;
  return (
    <section className="mb-10">
      <div className="flex items-baseline gap-3 mb-1">
        <h2 className="text-2xl font-bold">{title}</h2>
        <span className="badge badge-ghost badge-sm">{count}</span>
      </div>
      <p className="opacity-60 text-sm mb-4">{caption}</p>
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">{children}</div>
    </section>
  );
}

/** A result that lives on the hub — an internal route. */
function HitCard({
  to,
  title,
  body,
  tags,
}: {
  to: string;
  title: string;
  body: string;
  tags?: string[];
}) {
  return (
    <Link to={to} className="card bg-base-200 hover:shadow-md transition-shadow">
      <div className="card-body p-4">
        <h3 className="font-semibold">{title}</h3>
        {body && <p className="text-sm opacity-70 line-clamp-2">{body}</p>}
        {tags && tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {tags.map((tag) => (
              <span key={tag} className="badge badge-outline badge-sm">
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}

/** A result that lives somewhere else — the catalog, or a publisher. Opened in
 * a new tab, and `rel="noreferrer"` because a literature URL is a third party
 * we do not vouch for. */
function ExternalHitCard({
  href,
  title,
  body,
  badge,
}: {
  href: string;
  title: string;
  body: string;
  badge?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="card bg-base-200 hover:shadow-md transition-shadow"
    >
      <div className="card-body p-4">
        <h3 className="font-semibold">
          {title}
          {badge && <span className="badge badge-ghost badge-sm ml-2 align-middle">{badge}</span>}
          <span className="opacity-40 text-xs ml-1">↗</span>
        </h3>
        {body && <p className="text-sm opacity-70 line-clamp-2">{body}</p>}
      </div>
    </a>
  );
}

/** The `/search?q=` page the header's "search everything" row lands on.
 *
 * Same endpoint as the dropdown, just a bigger `limit` and room to show each
 * result's description — the dropdown is for a hit you can already name, this
 * is for browsing what matched. */
export default function Search() {
  const [params, setParams] = useSearchParams();
  // The only text input is the header's search bar, which is on every page — a
  // second box here would be two places to type the same query, and the one
  // in the header already has the dropdown.
  const query = (params.get("q") ?? "").trim();
  // `null` for an empty `?q=`: the page says what to do instead of asking the
  // server to match nothing. Note the deps: the type filter is not in them,
  // because it filters what this fetch already returned rather than re-running
  // it — ticking a box costs nothing and cannot fail.
  const { data, loading, error } = useFetch<SearchResults | null>(
    () => (query ? fetchSearch(query, PAGE_LIMIT) : Promise.resolve(null)),
    [query],
  );

  // The selection lives in the URL, so a filtered search is a link someone can
  // send. An unknown id is dropped rather than trusted: `types=` is editable
  // by hand, and a typo should not hide every section.
  const known = new Set(FACETS.map((facet) => facet.id as string));
  const selected = new Set((params.get("types") ?? "").split(",").filter((id) => known.has(id)));
  const shows = (id: string) => selected.size === 0 || selected.has(id);

  const writeTypes = (ids: Set<string>) => {
    const next = new URLSearchParams(params);
    // Order the ids by `FACETS` rather than by click order, so the same
    // selection is always the same URL.
    const ordered = FACETS.filter((facet) => ids.has(facet.id)).map((facet) => facet.id);
    if (ordered.length === 0) next.delete("types");
    else next.set("types", ordered.join(","));
    // `replace`: narrowing a search is refining one view, not visiting pages,
    // and the back button should return to wherever the search came from.
    setParams(next, { replace: true });
  };

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (!next.delete(id)) next.add(id);
    writeTypes(next);
  };

  // What is on screen, which is what "nothing matched" has to be about.
  const shown = data
    ? FACETS.reduce((sum, facet) => sum + (shows(facet.id) ? facet.count(data) : 0), 0)
    : 0;
  const matched = data ? FACETS.reduce((sum, facet) => sum + facet.count(data), 0) : 0;

  return (
    // No centering container: the rail belongs against the left edge, with the
    // results taking whatever is left of the width.
    <div className="flex flex-col lg:flex-row gap-8 px-2 py-10 ml-4">
      {/* Rendered for any query, not only once results land, so the rail does
          not pop in and shove the results sideways mid-load. */}
      {query && (
        <TypeFilter
          results={data}
          selected={selected}
          onToggle={toggle}
          onClear={() => writeTypes(new Set())}
        />
      )}

      <div className="min-w-0 grow">
        <h1 className="text-4xl font-bold mb-8">
          {query ? (
            <>
              Results for <span className="text-primary">{query}</span>
            </>
          ) : (
            "Search"
          )}
        </h1>

        {!query && (
          <div className="alert">Search from the box in the header to see results here.</div>
        )}
        {loading && query && <span className="loading loading-spinner loading-lg" />}
        {error && <div className="alert alert-error">{error}</div>}

        {data && (
          <>
            {matched === 0 && <div className="alert">Nothing matched “{query}”.</div>}
            {matched > 0 && shown === 0 && (
              <div className="alert">
                {/* The query is already in the heading, so this says what the
                    filter did rather than repeating it. */}
                <span>
                  {matched} {matched === 1 ? "result" : "results"}, none of the selected types.
                </span>
                <button type="button" className="btn btn-sm" onClick={() => writeTypes(new Set())}>
                  Clear filter
                </button>
              </div>
            )}

            <Section
              title="Topics"
              caption="Subject areas, each gathering its own metrics, data, dashboards and papers."
              count={shows("topic") ? data.topics.length : 0}
            >
              {data.topics.map((topic) => (
                <HitCard
                  key={topic.id}
                  to={`/topics/${encodeURIComponent(topic.name)}`}
                  title={topic.name}
                  body={topic.description}
                />
              ))}
            </Section>

            <Section
              title="Dashboards"
              caption="Published charts, each carrying the metric query that produced it."
              count={shows("dashboard") ? data.dashboards.length : 0}
            >
              {data.dashboards.map((dashboard) => (
                <HitCard
                  key={dashboard.id}
                  to={`/dashboards/${encodeURIComponent(dashboard.name)}`}
                  title={dashboard.title}
                  body={dashboard.description}
                  tags={dashboard.topics}
                />
              ))}
            </Section>

            <Section
              title="Datasets"
              caption="Tables in the catalog — these open in OpenMetadata."
              count={shows("dataset") ? datasets(data).length : 0}
            >
              {datasets(data).map((asset) => (
                <ExternalHitCard
                  key={asset.id}
                  href={asset.catalog_url}
                  title={asset.name}
                  body={asset.description}
                />
              ))}
            </Section>

            <Section
              title="Metrics"
              caption="Defined measures in the catalog, the same ones the charts are built from."
              count={shows("metric") ? metrics(data).length : 0}
            >
              {metrics(data).map((asset) => (
                <ExternalHitCard
                  key={asset.id}
                  href={asset.catalog_url}
                  title={asset.name}
                  body={asset.description}
                />
              ))}
            </Section>

            <Section
              title="Literature"
              caption="Curated and trending studies, filed under the same topics as the data."
              count={shows("paper") ? data.literature.length : 0}
            >
              {data.literature.map((item) => (
                <ExternalHitCard
                  key={item.id}
                  href={item.url}
                  title={item.title}
                  body={
                    item.summary ||
                    [item.authors, item.venue, item.year].filter(Boolean).join(" · ")
                  }
                  badge={item.trending ? "🔥 trending" : undefined}
                />
              ))}
            </Section>
          </>
        )}
      </div>
    </div>
  );
}
