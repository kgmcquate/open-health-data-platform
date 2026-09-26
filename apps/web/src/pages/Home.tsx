import { Link } from "react-router-dom";
import { fetchDashboards, fetchTopics } from "../lib/api";
import { useFetch } from "../lib/useFetch";
import GraphBackground from "../components/GraphBackground";
import SearchBar from "../components/SearchBar";

const HIGHLIGHTS = [
  {
    icon: "🏷️",
    title: "Explore by topic",
    body: "Infectious disease, respiratory illness, behavioral health, and more — each topic gathers its own metrics, data assets, dashboards, and literature in one place.",
    to: "/topics",
    cta: "Browse topics",
  },
  {
    icon: "📊",
    title: "Dashboards with a paper trail",
    body: "Every visualization carries the exact metric query that produced it. Curated, reproducible, and re-runnable — ask the chatbot to draw its own.",
    to: "/dashboards",
    cta: "See curated dashboards",
  },
  {
    icon: "💬",
    title: "A chatbot on rails",
    body: "The assistant answers only from curated metrics and literature — never invented SQL, never unsourced claims. Tool calls, thinking and citations stream live.",
    to: "/chat",
    cta: "Ask a question",
  },
  // {
  //   icon: "🛠️",
  //   title: "Build your own",
  //   body: "Write a dashboard spec — one metric query plus a Vega-Lite chart — and watch it render as you type. Keep it as a draft, or publish it to the library for everyone.",
  //   to: "/dashboards/new",
  //   cta: "Open the builder",
  // },
  {
    icon: "📚",
    title: "Literature, trend-watched",
    body: "Trending and curated studies sit next to the data they cite, filed under the same topic — reading and verifying are one click apart.",
    to: "/topics",
    cta: "Find a topic's papers",
  },
];

/** What a dashboard spec looks like, for the builder pitch below. Deliberately
 * the whole thing rather than an excerpt: "it is this short" is the point, and a
 * spec of this shape is exactly what `/dashboards/new` renders and publishes. */
const SPEC_SNIPPET = `name: flu-ed-visits-by-week
title: Flu ED visit share, by week
query:
  measures:
    - ed_visits.avg_percent
  time_dimensions:
    - dimension: ed_visits.week_end
      granularity: week
vega_lite:
  mark: line
  encoding:
    x: { field: ed_visits.week_end, type: temporal }
    y: { field: ed_visits.avg_percent, type: quantitative }`;

export default function Home() {
  const topics = useFetch(fetchTopics);
  const dashboards = useFetch(fetchDashboards);
  const featured = dashboards.data?.find((d) => d.featured);
  const sampleTopics = topics.data?.slice(0, 5) ?? [];

  return (
    <div>
      {/* `overflow-hidden` is on the background's own wrapper, not the
          section: the search dropdown has to be free to hang past the hero's
          bottom edge rather than be clipped by it. `z-20` keeps that dropdown
          over the highlights section below. */}
      <section className="hero relative z-20 bg-gradient-to-br from-primary/15 via-base-100 to-accent/10 min-h-[32rem]">
        <div className="absolute inset-0 overflow-hidden">
          <GraphBackground className="opacity-70" />
        </div>
        <div className="hero-content relative z-10 text-center w-full max-w-4xl py-16">
          <div className="card w-full bg-base-100/50 backdrop-blur-sm shadow-xl px-6 py-10 sm:px-10">
            {/* <div className="badge badge-primary badge-outline mb-4">
              Built in the open · Visible from the source, through the plumbing, and into the dashboard
            </div> */}
            <h1 className="text-5xl font-extrabold leading-tight">
              Health data analysis
              <br/>
              <span className="text-primary"> - for everyone.</span>
            </h1>
            <p className="py-6 text-lg opacity-80">
              An open analytics platform for public health data.
            </p>
            <SearchBar size="lg" />
          </div>
        </div>
      </section>

      <section className="max-w-6xl mx-auto px-4 py-12 grid gap-6 md:grid-cols-2">
        {HIGHLIGHTS.map((h) => (
          <div key={h.title} className="card bg-base-200 shadow-sm hover:shadow-md transition-shadow">
            <div className="card-body">
              <h2 className="card-title">
                <span className="text-2xl">{h.icon}</span> {h.title}
              </h2>
              <p className="opacity-80">{h.body}</p>
              <div className="card-actions justify-end">
                <Link to={h.to} className="btn btn-sm btn-ghost text-primary">
                  {h.cta} →
                </Link>
              </div>
            </div>
          </div>
        ))}
      </section>

      {/* "Create your own dashboard": the third way a chart gets onto this
       * platform, after ours and the chatbot's. The snippet is real — a spec
       * of exactly this shape is what the builder posts, renders and can
       * publish (`hub_api.builder`) — because the whole pitch is that a
       * dashboard here is short, readable and reproducible. */}
      <section className="max-w-6xl mx-auto px-4 pb-12">
        <div className="card bg-base-200 shadow-sm">
          <div className="card-body gap-6 lg:grid lg:grid-cols-2 lg:items-center">
            <div>
              <h2 className="card-title text-2xl">
                <span className="text-2xl">🛠️</span> Create your own dashboard
              </h2>
              <p className="opacity-80 mt-2">
                A dashboard here is a <span className="font-semibold">question, not a picture</span>
                : one query against the semantic layer plus a Vega-Lite spec for drawing its rows.
                Write one in the browser and it renders live — by the same code that draws every
                published chart, against the same curated metrics, so the numbers cannot come from
                anywhere else.
              </p>
              <p className="opacity-80 mt-2 text-sm">
                Save drafts while you work on it, then publish it to the library when it is worth
                showing — where it sits alongside ours and the chatbot's, and readers vote.
              </p>
              <div className="card-actions mt-4">
                <Link to="/dashboards/new" className="btn btn-primary btn-sm">
                  Build a dashboard
                </Link>
                <Link to="/dashboards" className="btn btn-ghost btn-sm">
                  See what others published
                </Link>
              </div>
            </div>
            <pre className="bg-base-300 rounded-box p-4 text-xs overflow-x-auto leading-relaxed">
              <code>{SPEC_SNIPPET}</code>
            </pre>
          </div>
        </div>
      </section>

      {(sampleTopics.length > 0 || featured) && (
        <section className="max-w-6xl mx-auto px-4 pb-12 grid gap-6 md:grid-cols-2">
          {sampleTopics.length > 0 && (
            <div className="card bg-primary text-primary-content shadow">
              <div className="card-body">
                <h3 className="card-title text-sm uppercase tracking-wide opacity-80">
                  Browse a topic
                </h3>
                <div className="flex flex-wrap gap-2 mt-1">
                  {sampleTopics.map((topic) => (
                    <Link
                      key={topic.id}
                      to={`/topics/${encodeURIComponent(topic.name)}`}
                      className="btn btn-sm btn-secondary"
                    >
                      {topic.name}
                    </Link>
                  ))}
                </div>
                <div className="card-actions justify-end mt-3">
                  <Link to="/topics" className="btn btn-sm btn-ghost">
                    All topics
                  </Link>
                </div>
              </div>
            </div>
          )}
          {featured && (
            <div className="card bg-secondary text-secondary-content shadow">
              <div className="card-body">
                <h3 className="card-title text-sm uppercase tracking-wide opacity-80">
                  Featured dashboard
                </h3>
                <p className="text-xl font-bold">{featured.title}</p>
                <p className="opacity-90 line-clamp-3">{featured.description}</p>
                <div className="card-actions justify-end">
                  <Link to="/dashboards" className="btn btn-sm btn-accent">
                    View dashboards
                  </Link>
                </div>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
