import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          className="w-4 h-4"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
        </svg>
      </button>
      <span className="text-sm tabular-nums w-6 text-center">{score}</span>
      <button
        className={`btn btn-ghost btn-xs ${vote === -1 ? "text-error" : ""}`}
        onClick={() => void cast(-1)}
        disabled={disabled}
        title={user ? "Not useful" : "Sign in to vote"}
        aria-label="Vote down"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          className="w-4 h-4"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
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
  const [copied, setCopied] = useState(false);

  async function copyPermalink() {
    const path = `/dashboards/${encodeURIComponent(dashboard.name)}`;
    const url = `${window.location.origin}${path}`;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement("textarea");
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore copy failures silently for now
    }
  }
  const navigate = useNavigate();

  function startChat() {
    const path = `/dashboards/${encodeURIComponent(dashboard.name)}`;
    const queryJson = JSON.stringify(dashboard.query ?? {}, null, 2);
    const question = `Discuss the dashboard "${dashboard.title}" at ${window.location.origin}${path}\n\nQuery JSON:\n\`\`\`json\n${queryJson}\n\`\`\``;
    navigate(`/chat?q=${encodeURIComponent(question)}`);
  }

  function ChatAndVotes({ dashboard }: { dashboard: Dashboard }) {
    return (
      <div className="flex items-center">
        <button
          className="btn btn-ghost btn-xs"
          title="Start a chat about this dashboard"
          aria-label="Chat about dashboard"
          onClick={() => void startChat()}
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="w-4 h-4" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <div role="separator" aria-hidden="true" className="w-px h-5 bg-gray-300 dark:bg-gray-600 mx-2 self-center" />
        <Votes dashboard={dashboard} />
      </div>
    );
  }
  return (
    <div className="card bg-base-200 shadow-sm">
      <div>
        {/* The embed renders the dashboard's own title, description, caption,
         * Cube query and YAML source, so the card carries only what the stored
         * page cannot know about itself: how it got here, which topics claim
         * it, and what this visitor may do to it. */}

        <DashboardEmbed src={dashboardHtmlUrl(dashboard.name)} title={dashboard.title} />

        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-2 flex-wrap">
            {dashboard.featured && <span className="badge badge-accent">featured</span>}
            {/* {dashboard.source === "chat" && (
              <span className="badge badge-ghost" title="Saved from a chat conversation">
                from chat
              </span>
            )} */}
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
          <div className="flex flex-col items-end gap-1 flex-1">

            <div className="flex items-center gap-1 mt-2 mb-2 w-full">
              <p className="text-xs opacity-60 mr-auto text-left">
                Data as of {renderedAgo(dashboard.last_rendered)}
                {dashboard.stale && " — refreshing for the next visitor"}
              </p>
              <button
                className="btn btn-ghost btn-xs"
                title="Copy permalink to clipboard"
                aria-label="Copy permalink"
                onClick={() => void copyPermalink()}
              >
                {copied ? (
                  <span className="flex items-center gap-1">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      className="w-4 h-4 text-success"
                      aria-hidden="true"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    <span className="text-xs">Copied</span>
                  </span>
                ) : (
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    className="w-4 h-4 text-primary"
                    aria-hidden="true"
                  >
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} />
                  </svg>
                )}
              </button>
              <div
                role="separator"
                aria-hidden="true"
                className="w-px h-5 bg-gray-300 dark:bg-gray-600 mx-2 self-center"
              />
              <ChatAndVotes dashboard={dashboard} />
            </div>
            <DeleteDashboard dashboard={dashboard} onDeleted={onDeleted} />
          </div>
        </div>

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
      <div className="flex items-end justify-between gap-4 mb-2 flex-wrap">
        <h1 className="text-4xl font-bold">Dashboards</h1>
        {/* The third way a chart gets onto this page, next to ours and the
         * agent's: someone writing the spec themselves (`/dashboards/new`). */}
        <Link to="/dashboards/new" className="btn btn-primary btn-sm">
          Create your own
        </Link>
      </div>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {shown && shown.length === 0 && (
        <div className="alert">
          No dashboards yet — ask the chatbot to draw something and save it, or{" "}
          <Link to="/dashboards/new" className="link">
            write a spec yourself
          </Link>
          , and it will show up here.
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
