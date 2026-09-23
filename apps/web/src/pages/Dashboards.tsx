import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { DashboardEmbed } from "../components/DashboardEmbed";
import {
  dashboardHtmlUrl,
  deleteDashboard,
  fetchDashboards,
  voteDashboard,
  type Dashboard,
} from "../lib/api";
import { useFetch } from "../lib/useFetch";

/** How long ago the stored page was rendered, in words. The numbers in a
 * dashboard are as old as its render, and a reader should not have to guess
 * how old that is. */
function renderedAgo(iso: string | null): string {
  if (!iso) return "not yet rendered";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Up/down votes. Signed-out visitors see the score but cannot vote — one row
 * per person per dashboard is the only thing keeping the ranking honest. */
function Votes({ dashboard }: { dashboard: Dashboard }) {
  const { user } = useAuth();
  const [vote, setVote] = useState(dashboard.my_vote);
  const [score, setScore] = useState(dashboard.score);
  const [failed, setFailed] = useState(false);

  async function cast(value: 1 | -1) {
    // Clicking the way you already voted withdraws it.
    const next = vote === value ? 0 : value;
    const previous = { vote, score };
    setVote(next);
    setScore(score - vote + next);
    setFailed(false);
    try {
      const updated = await voteDashboard(dashboard.name, next);
      setVote(updated.my_vote);
      setScore(updated.score);
    } catch {
      setVote(previous.vote);
      setScore(previous.score);
      setFailed(true);
    }
  }

  const disabled = !user;

  return (
    <div className="flex items-center gap-1">
      <button
        className={`btn btn-ghost btn-xs ${vote === 1 ? "text-success" : ""}`}
        onClick={() => void cast(1)}
        disabled={disabled}
        title={user ? "Useful" : "Sign in to vote"}
        aria-label="Vote up"
      >
        ▲
      </button>
      <span className="text-sm tabular-nums w-6 text-center">{score}</span>
      <button
        className={`btn btn-ghost btn-xs ${vote === -1 ? "text-error" : ""}`}
        onClick={() => void cast(-1)}
        disabled={disabled}
        title={user ? "Not useful" : "Sign in to vote"}
        aria-label="Vote down"
      >
        ▼
      </button>
      {failed && <span className="text-xs text-error">vote failed</span>}
    </div>
  );
}

/** Take a dashboard down. Admins only, and only rendered for them — but the
 * route checks the role itself, so this is about not offering a button that
 * would 403, not about who can actually delete.
 *
 * Two clicks, not a `window.confirm`: the row, its stored render and its
 * votes go, and nothing anywhere can rebuild the spec. Confirming inline
 * keeps that warning next to the chart it is about instead of in a browser
 * dialog that names nothing. */
function DeleteDashboard({
  dashboard,
  onDeleted,
}: {
  dashboard: Dashboard;
  onDeleted?: (name: string) => void;
}) {
  const { user } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  if (!user?.is_admin) return null;

  async function remove() {
    setBusy(true);
    setFailed(null);
    try {
      await deleteDashboard(dashboard.name);
      // The parent drops the card. Nothing is re-fetched: the row is gone, so
      // there is no fresher server state to go and read.
      onDeleted?.(dashboard.name);
    } catch (exc) {
      setFailed(exc instanceof Error ? exc.message : "Delete failed");
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  if (!confirming) {
    return (
      <div className="flex flex-col items-end">
        <button
          className="btn btn-ghost btn-xs text-error"
          onClick={() => setConfirming(true)}
          title="Delete this dashboard (admin)"
        >
          Delete
        </button>
        {failed && <span className="text-xs text-error">{failed}</span>}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1">
      <span className="text-xs opacity-70">Delete for good?</span>
      <button
        className="btn btn-error btn-xs"
        onClick={() => void remove()}
        disabled={busy}
      >
        {busy ? "Deleting…" : "Yes, delete"}
      </button>
      <button
        className="btn btn-ghost btn-xs"
        onClick={() => setConfirming(false)}
        disabled={busy}
      >
        Cancel
      </button>
    </div>
  );
}

// Exported so TopicDetail can render a topic's own dashboards with the same
// card (including the live chart) instead of a copy of it.
//
// `onDeleted` is how the page that owns the list hears about an admin delete —
// the card cannot drop itself.
export function DashboardCard({
  dashboard,
  onDeleted,
}: {
  dashboard: Dashboard;
  onDeleted?: (name: string) => void;
}) {
  return (
    <div className="card bg-base-200 shadow-sm">
      <div className="card-body">
        {/* The embed renders the dashboard's own title, description, caption,
         * Cube query and YAML source, so the card carries only what the stored
         * page cannot know about itself: how it got here, which topics claim
         * it, and what this visitor may do to it. */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-2 flex-wrap">
            {dashboard.featured && <span className="badge badge-accent">featured</span>}
            {dashboard.source === "chat" && (
              <span className="badge badge-ghost" title="Saved from a chat conversation">
                from chat
              </span>
            )}
            {dashboard.topics.map((topic) => (
              <Link
                key={topic}
                to={`/topics/${encodeURIComponent(topic)}`}
                className="badge badge-outline badge-sm"
              >
                {topic}
              </Link>
            ))}
          </div>
          <div className="flex flex-col items-end gap-1">
            <Votes dashboard={dashboard} />
            <DeleteDashboard dashboard={dashboard} onDeleted={onDeleted} />
          </div>
        </div>

        <div className="mt-4">
          <DashboardEmbed src={dashboardHtmlUrl(dashboard.name)} title={dashboard.title} />
        </div>

        <p className="text-xs opacity-60">
          Data as of {renderedAgo(dashboard.last_rendered)}
          {dashboard.stale && " — refreshing for the next visitor"}
        </p>
      </div>
    </div>
  );
}

export default function Dashboards() {
  const { data, loading, error } = useFetch<Dashboard[]>(fetchDashboards);
  // Names an admin has deleted this visit. Kept beside `useFetch`'s result
  // rather than refetching the list: the row is gone server-side, and a
  // refetch would redraw every other card's iframe to learn that.
  const [deleted, setDeleted] = useState<string[]>([]);
  const shown = data?.filter((dashboard) => !deleted.includes(dashboard.name));

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Dashboards</h1>
      <p className="opacity-70 mb-8 max-w-2xl">
        Visualizations over the semantic layer — ours, and the ones the
        assistant saved from a conversation. Every dashboard shows the exact
        metric query behind it and says how fresh its numbers are; opening an
        old one refreshes it for the next visitor. Vote them up or down: the
        ones people find useful rise, and the ones they don't drop off this
        page.
      </p>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {shown && shown.length === 0 && (
        <div className="alert">
          No dashboards yet — ask the chatbot to draw something and save it, and
          it will show up here.
        </div>
      )}

      <div className="space-y-6">
        {shown?.map((dashboard) => (
          <DashboardCard
            key={dashboard.id}
            dashboard={dashboard}
            onDeleted={(name) => setDeleted((names) => [...names, name])}
          />
        ))}
      </div>
    </div>
  );
}
