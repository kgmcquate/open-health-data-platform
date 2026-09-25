import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fetchChatAllowance, submitIssue, type ChatAllowance, type IssueKind, type IssueResponse } from "../lib/api";

const KINDS: { id: IssueKind; label: string; hint: string }[] = [
  { id: "bug", label: "Bug", hint: "Something is broken or behaves unexpectedly." },
  { id: "data-quality", label: "Data quality", hint: "A number or dataset looks wrong, stale, or missing." },
  { id: "feature", label: "Feature request", hint: "Something you wish the platform could do." },
];

const TITLE_MIN = 8;
const BODY_MIN = 20;

// The placeholder line Chat.tsx's "report a problem" button prepends before
// quoting recent chat — matched here so the pre-filled body is selected
// rather than just focused, letting the first keystroke replace it the way a
// normal placeholder would in an empty field.
const REPORT_PLACEHOLDER = "Describe what went wrong here.";

interface SupportNavState {
  prefillKind?: IssueKind;
  prefillBody?: string;
}

export default function Support() {
  const { user, signIn } = useAuth();
  const location = useLocation();
  const prefill = location.state as SupportNavState | null;
  const [kind, setKind] = useState<IssueKind>(prefill?.prefillKind ?? "bug");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState(prefill?.prefillBody ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IssueResponse | null>(null);
  const [allowance, setAllowance] = useState<ChatAllowance | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (user) fetchChatAllowance().then(setAllowance).catch(() => setAllowance(null));
  }, [user]);

  // Runs once, only when the page opened with chat context to prefill: puts
  // the cursor in the body field with the placeholder line selected, so
  // typing immediately replaces it instead of just inserting next to it.
  useEffect(() => {
    if (!prefill?.prefillBody?.startsWith(REPORT_PLACEHOLDER)) return;
    const textarea = bodyRef.current;
    if (!textarea) return;
    textarea.focus();
    textarea.setSelectionRange(0, REPORT_PLACEHOLDER.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- prefill is read
    // once, from the navigation that landed on this page, not on every render.
  }, []);

  const outOfReports = Boolean(allowance && allowance.issues_used_today >= allowance.issues_allowed_per_day);
  const canSubmit =
    Boolean(user) &&
    !outOfReports &&
    title.trim().length >= TITLE_MIN &&
    body.trim().length >= BODY_MIN &&
    !busy;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      setResult(await submitIssue(title.trim(), body.trim(), kind));
      setTitle("");
      setBody("");
      // Refreshes the count shown below — a duplicate report doesn't consume
      // it, so this can come back unchanged after a submit.
      void fetchChatAllowance().then(setAllowance).catch(() => undefined);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not submit that report.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Support</h1>
      <p className="opacity-70 mb-8">
        Report a bug, flag a data quality issue, or request a feature. Reports
        are filed publicly, so please don't include credentials or personal health information.
      </p>

      {user === null && (
        <div className="alert mb-6">
          <span>Sign in to submit a report.</span>
          <button className="btn btn-primary btn-sm" onClick={signIn}>
            Sign in
          </button>
        </div>
      )}

      {outOfReports && (
        <div className="alert mb-6">
          <span>
            You've used today's report allowance ({allowance?.issues_used_today}/
            {allowance?.issues_allowed_per_day}). Try again tomorrow.
          </span>
        </div>
      )}

      {result && (
        <div className="alert alert-success mb-6">
          <span>
            {result.duplicate ? "Already reported — this matches an open issue: " : "Filed as "}
            <a className="link font-medium" href={result.url} target="_blank" rel="noreferrer">
              #{result.number}
            </a>
          </span>
        </div>
      )}

      <div className="space-y-4">
        <div className="join">
          {KINDS.map((k) => (
            <button
              key={k.id}
              type="button"
              className={`join-item btn btn-sm ${kind === k.id ? "btn-active" : "btn-ghost"}`}
              onClick={() => setKind(k.id)}
              disabled={busy}
            >
              {k.label}
            </button>
          ))}
        </div>
        <p className="text-xs opacity-60">{KINDS.find((k) => k.id === kind)?.hint}</p>

        <fieldset className="fieldset">
          <legend className="fieldset-legend">Title</legend>
          <input
            type="text"
            className="input input-bordered w-full"
            placeholder="Short summary of the problem"
            value={title}
            maxLength={300}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
          />
        </fieldset>

        <fieldset className="fieldset">
          <legend className="fieldset-legend">Details</legend>
          {prefill?.prefillBody && (
            <p className="mb-1 text-xs opacity-60">
              Pre-filled with recent messages from your chat — this will be public, so review and
              edit it before submitting.
            </p>
          )}
          <textarea
            ref={bodyRef}
            className="textarea textarea-bordered w-full h-40"
            placeholder="What were you doing, what did you expect, and what happened instead?"
            value={body}
            maxLength={20000}
            onChange={(e) => setBody(e.target.value)}
            disabled={busy}
          />
        </fieldset>

        {error && <div className="alert alert-error text-sm">{error}</div>}

        <div className="flex items-center gap-3">
          <button className="btn btn-primary" disabled={!canSubmit} onClick={() => void submit()}>
            {busy ? "Submitting…" : "Submit report"}
          </button>
          {allowance && (
            <span className="text-xs opacity-60">
              {allowance.issues_used_today} / {allowance.issues_allowed_per_day} used today
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
