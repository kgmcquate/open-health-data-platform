import { Link } from "react-router-dom";
import { fetchNews, fetchDashboards } from "../lib/api";
import { useFetch } from "../lib/useFetch";
import GraphBackground from "../components/GraphBackground";

const HIGHLIGHTS = [
  {
    icon: "🌍",
    title: "Public data, one spine",
    body: "OpenAQ, CDC, openFDA, CMS, WHO GHO, and more - served through a single semantic layer, so that every number means the same thing everywhere.",
    to: "/data-sources",
    cta: "Explore the sources",
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
  {
    icon: "📚",
    title: "Literature, trend-watched",
    body: "Trending and curated studies sit next to the data they cite, so reading and verifying are one click apart.",
    to: "/literature",
    cta: "Browse literature",
  },
];

export default function Home() {
  const news = useFetch(fetchNews);
  const dashboards = useFetch(fetchDashboards);
  const latest = news.data?.[0];
  const featured = dashboards.data?.find((d) => d.featured);

  return (
    <div>
      <section className="hero relative overflow-hidden bg-gradient-to-br from-primary/15 via-base-100 to-accent/10 min-h-[32rem]">
        <GraphBackground className="opacity-70" />
        <div className="hero-content relative z-10 text-center max-w-3xl py-16">
          <div className="card bg-base-100/50 backdrop-blur-sm shadow-xl px-6 py-10 ">
            {/* <div className="badge badge-primary badge-outline mb-4">
              Built in the open · Visible from the source, through the plumbing, and into the dashboard
            </div> */}
            <h1 className="text-5xl font-extrabold leading-tight">
              Massive health insights
              <span className="text-primary"> - minus the effort.</span>
            </h1>
            <p className="py-6 text-lg opacity-80">
              An open analytics platform for public health data.
            </p>
            <div className="flex gap-3 justify-center">
              <Link to="/chat" className="btn btn-primary">
                Try the chatbot
              </Link>
              <Link to="/data-sources" className="btn btn-outline">
                What's inside?
              </Link>
            </div>
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

      {(latest || featured) && (
        <section className="max-w-6xl mx-auto px-4 pb-12 grid gap-6 md:grid-cols-2">
          {latest && (
            <div className="card bg-primary text-primary-content shadow">
              <div className="card-body">
                <h3 className="card-title text-sm uppercase tracking-wide opacity-80">
                  Latest news
                </h3>
                <p className="text-xl font-bold">{latest.title}</p>
                <p className="opacity-90 line-clamp-3">{latest.summary}</p>
                <div className="card-actions justify-end">
                  <Link to="/news" className="btn btn-sm btn-secondary">
                    All news
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
