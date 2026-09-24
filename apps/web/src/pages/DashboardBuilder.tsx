import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { DashboardEmbed } from "../components/DashboardEmbed";
import {
  createDraft,
  deleteDraft,
  fetchCubes,
  fetchDrafts,
  fetchMyDashboards,
  previewDashboard,
  publishDashboard,
  updateDraft,
  type CubeRef,
  type DashboardDraft,
  type DashboardPreview,
  type MyDashboard,
  type PublishResult,
} from "../lib/api";

/** Write a dashboard by hand, see it drawn, keep it, publish it.
 *
 * The same `DashboardSpec` the chat agent writes (`ohdp_agent.dashboard`), in
 * the same YAML a rendered card shows as its own source — so a spec written
 * here, saved by the agent, or committed to the repo are one artifact with one
 * set of rules. This page holds none of them: it posts the text to
 * `/api/builder/*` and shows what comes back (`hub_api.builder`).
 *
 * Two consequences of that split are worth knowing before changing anything
 * here:
 *
 *   - **The preview is a page, not a chart.** The server runs the Cube query,
 *     binds the rows and renders the same document a published dashboard is
 *     served as; this page drops it into a sandboxed iframe. So the editor
 *     needs no Vega, no YAML parser and no schema copy, and "what you see" is
 *     literally what a visitor would get.
 *   - **Every error message is the server's.** A YAML syntax error, the spec
 *     field pydantic rejected, or the semantic layer's own words about a
 *     member that does not exist — all of them are more useful than anything
 *     this page could say, so they are shown verbatim (`lib/api`'s `failure`).
 */

const FALLBACK_STARTER = `# A dashboard is one semantic-layer query plus a Vega-Lite spec.
# The query's rows are bound by the server — a spec may not carry data.
name: my-first-dashboard
title: A title that names what is being counted
description: The question this chart answers, for someone browsing the library.
query:
  measures:
    - cube_name.some_measure
  time_dimensions:
    - dimension: cube_name.some_date
      granularity: week
vega_lite:
  mark: line
  encoding:
    x:
      field: cube_name.some_date
      type: temporal
      title: Week ending
    y:
      field: cube_name.some_measure
      type: quantitative
      title: Give every axis a title that carries its units
`;

/** A starter spec built from a real cube, so the first render draws something
 * instead of failing on a placeholder. Falls back to the template above when
 * the semantic layer could not be reached — the author then has the shape and
 * the field reference, which is still better than an empty box. */
function starterSpec(cubes: CubeRef[] | null): string {
  const cube =
    cubes?.find((c) => c.measures.length > 0 && c.dimensions.some((d) => d.type === "time")) ??
    cubes?.find((c) => c.measures.length > 0);
  const measure = cube?.measures[0];
  const time = cube?.dimensions.find((d) => d.type === "time");
  if (!cube || !measure || !time) return FALLBACK_STARTER;

  return `# A dashboard is one semantic-layer query plus a Vega-Lite spec.
# The query's rows are bound by the server — a spec may not carry data.
name: my-first-dashboard
title: ${measure.title || measure.name} by week
description: The question this chart answers, for someone browsing the library.
query:
  measures:
    - ${measure.name}
  time_dimensions:
    - dimension: ${time.name}
      granularity: week
vega_lite:
  mark: line
  encoding:
    x:
      field: ${time.name}
      type: temporal
      title: ${time.title || "Date"}
    y:
      field: ${measure.name}
      type: quantitative
      title: ${measure.title || measure.name}
`;
}

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Every member a query may name, straight from Cube's `/meta` — the same
 * world the chat agent works in. A mistyped member is the most common way a
 * hand-written spec comes back empty, so each name is a click-to-copy button
 * rather than something to retype. */
function FieldReference({ cubes }: { cubes: CubeRef[] | null }) {
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(name: string) {
    try {
      await navigator.clipboard?.writeText(name);
      setCopied(name);
      setTimeout(() => setCopied((current) => (current === name ? null : current)), 1500);
    } catch {
      // A browser that refuses the clipboard still shows the name to select.
    }
  }

  if (!cubes) return null;

  return (
    <div className="space-y-2">
      {cubes.map((cube) => (
        <details key={cube.name} className="collapse collapse-arrow bg-base-200">
          <summary className="collapse-title text-sm font-medium">
            {cube.title || cube.name}
            <span className="ml-2 font-mono text-xs opacity-60">{cube.name}</span>
          </summary>
          <div className="collapse-content text-xs space-y-3">
            {cube.description && <p className="opacity-70">{cube.description}</p>}
            {[
              { label: "Measures", members: cube.measures },
              { label: "Dimensions", members: cube.dimensions },
            ].map(({ label, members }) =>
              members.length === 0 ? null : (
                <div key={label}>
                  <p className="font-semibold uppercase tracking-wide opacity-60">{label}</p>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {members.map((member) => (
                      <button
                        key={member.name}
                        className="badge badge-outline badge-sm font-mono hover:badge-primary"
                        title={member.description || `Copy ${member.name}`}
                        onClick={() => void copy(member.name)}
                      >
                        {copied === member.name ? "copied" : member.name}
                      </button>
                    ))}
                  </div>
                </div>
              ),
            )}
          </div>
        </details>
      ))}
    </div>
  );
}

/** Publishing, behind one deliberate extra click.
 *
 * Saving a draft is private and reversible; this is neither. A published
 * dashboard is on the Dashboards page and its topic's page immediately, for
 * anyone, under the author's chosen name — so the confirm step says that in
 * those words rather than asking "are you sure?". */
function PublishButton({
  specYaml,
  disabled,
  onPublished,
}: {
  specYaml: string;
  disabled: boolean;
  onPublished: (result: PublishResult) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function publish() {
    setBusy(true);
    setFailed(null);
    try {
      onPublished(await publishDashboard(specYaml));
      setConfirming(false);
    } catch (exc) {
      setFailed(exc instanceof Error ? exc.message : "Publishing failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      {confirming ? (
        <div className="flex items-center gap-2">
          <span className="text-xs opacity-70">
            This puts the chart on the public Dashboards page.
          </span>
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void publish()}>
            {busy ? "Publishing…" : "Yes, publish"}
          </button>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <button
          className="btn btn-primary btn-sm"
          disabled={disabled}
          onClick={() => {
            setFailed(null);
            setConfirming(true);
          }}
        >
          Publish…
        </button>
      )}
      {failed && <span className="text-xs text-error max-w-md text-right">{failed}</span>}
    </div>
  );
}

export default function DashboardBuilder() {
  const { user, signIn } = useAuth();
  const signedIn = Boolean(user);

  const [specYaml, setSpecYaml] = useState("");
  const [preview, setPreview] = useState<DashboardPreview | null>(null);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  const [cubes, setCubes] = useState<CubeRef[] | null>(null);
  const [drafts, setDrafts] = useState<DashboardDraft[]>([]);
  const [draftId, setDraftId] = useState<number | null>(null);
  const [draftSaved, setDraftSaved] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);

  const [mine, setMine] = useState<MyDashboard[]>([]);
  const [published, setPublished] = useState<PublishResult | null>(null);

  // Which render is current: a slow response for text the author has already
  // replaced must not overwrite a newer one.
  const runId = useRef(0);

  const render = useCallback(async (text: string) => {
    if (!text.trim()) return;
    const run = ++runId.current;
    setRendering(true);
    setRenderError(null);
    try {
      const drawnPage = await previewDashboard(text);
      if (run === runId.current) setPreview(drawnPage);
    } catch (exc) {
      if (run === runId.current) {
        setRenderError(exc instanceof Error ? exc.message : "That spec could not be drawn.");
      }
    } finally {
      if (run === runId.current) setRendering(false);
    }
  }, []);

  // The signed-in load: the field reference, this author's drafts, and what
  // they have already published. Each is independent — a semantic layer that
  // is down costs the reference panel, not the editor.
  useEffect(() => {
    if (!signedIn) return;
    void fetchCubes()
      .then(setCubes)
      .catch(() => setCubes(null));
    void fetchDrafts()
      .then(setDrafts)
      .catch(() => setDrafts([]));
    void fetchMyDashboards()
      .then(setMine)
      .catch(() => setMine([]));
  }, [signedIn]);

  // Fill an untouched editor with a starter built from a real cube. Only while
  // it is empty: this must never land on top of something the author typed, or
  // on a draft they loaded.
  useEffect(() => {
    setSpecYaml((current) => (current.trim() === "" ? starterSpec(cubes) : current));
  }, [cubes]);

  function loadInto(text: string, id: number | null) {
    setSpecYaml(text);
    setDraftId(id);
    setDraftSaved(null);
    setDraftError(null);
    setPublished(null);
    void render(text);
  }

  async function saveDraft() {
    setSavingDraft(true);
    setDraftError(null);
    try {
      const saved =
        draftId === null ? await createDraft(specYaml) : await updateDraft(draftId, specYaml);
      setDraftId(saved.id);
      setDraftSaved(saved.updated_at);
      setDrafts(await fetchDrafts());
    } catch (exc) {
      setDraftError(exc instanceof Error ? exc.message : "Could not save that draft.");
    } finally {
      setSavingDraft(false);
    }
  }

  async function removeDraft(id: number) {
    try {
      await deleteDraft(id);
      if (id === draftId) setDraftId(null);
      setDrafts(await fetchDrafts());
    } catch (exc) {
      setDraftError(exc instanceof Error ? exc.message : "Could not delete that draft.");
    }
  }

  function onPublished(result: PublishResult) {
    setPublished(result);
    void fetchMyDashboards()
      .then(setMine)
      .catch(() => undefined);
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-10 space-y-8">
      <header className="space-y-2">
        <h1 className="text-4xl font-bold">Create your own dashboard</h1>
        <p className="opacity-80 max-w-3xl">
          A dashboard here is <span className="font-semibold">a question, not a picture</span>: one
          query against the semantic layer, plus a{" "}
          <a
            href="https://vega.github.io/vega-lite/docs/"
            target="_blank"
            rel="noreferrer"
            className="link link-primary"
          >
            Vega-Lite
          </a>{" "}
          spec saying how to draw its rows. Write it below and press Render to see it — by the same
          code that draws every published chart, so the numbers come from the same place, and your
          spec cannot carry any of its own.
        </p>
      </header>

      {user === null && (
        <div className="alert">
          <span>Sign in to render, save and publish a dashboard.</span>
          <button className="btn btn-primary btn-sm" onClick={signIn}>
            Sign in
          </button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h2 className="text-lg font-semibold">
              Spec
              {draftId !== null && <span className="badge badge-ghost badge-sm ml-2">draft</span>}
            </h2>
          </div>

          <textarea
            className="textarea textarea-bordered w-full font-mono text-xs leading-relaxed"
            style={{ minHeight: "32rem" }}
            spellCheck={false}
            value={specYaml}
            onChange={(event) => setSpecYaml(event.target.value)}
            onKeyDown={(event) => {
              // Draw now, whatever the idle timer thinks — the shortcut every
              // editor with a preview pane has.
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                void render(specYaml);
              }
            }}
            aria-label="Dashboard spec (YAML)"
          />

          <div className="flex items-center gap-2 flex-wrap">
            <button
              className="btn btn-sm"
              disabled={!signedIn || rendering}
              onClick={() => void render(specYaml)}
            >
              {rendering ? "Drawing…" : "Render"}
            </button>
            <button
              className="btn btn-sm btn-ghost"
              disabled={!signedIn || savingDraft || !specYaml.trim()}
              onClick={() => void saveDraft()}
            >
              {draftId === null ? "Save as draft" : "Update draft"}
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => loadInto(starterSpec(cubes), null)}
              title="Start a new spec from the template"
            >
              New
            </button>
            <span className="text-xs opacity-60">
              {savingDraft ? "Saving…" : draftSaved ? `Draft saved ${when(draftSaved)}` : ""}
            </span>
            <div className="ml-auto">
              <PublishButton
                specYaml={specYaml}
                disabled={!signedIn || !specYaml.trim()}
                onPublished={onPublished}
              />
            </div>
          </div>

          {draftError && <div className="alert alert-warning text-sm">{draftError}</div>}

          {drafts.length > 0 && (
            <div className="card bg-base-200">
              <div className="card-body p-4 gap-2">
                <h3 className="text-sm font-semibold uppercase tracking-wide opacity-70">
                  Your drafts
                </h3>
                <p className="text-xs opacity-60">
                  Private to you, and free to be unfinished — a draft does not have to parse.
                </p>
                <ul className="divide-y divide-base-300">
                  {drafts.map((draft) => (
                    <li key={draft.id} className="flex items-center gap-2 py-2">
                      <button
                        className="text-left flex-1 hover:text-primary"
                        onClick={() => loadInto(draft.spec_yaml, draft.id)}
                      >
                        <span className="text-sm">{draft.title || "Untitled draft"}</span>
                        <span className="block text-xs opacity-60 font-mono">
                          {draft.name || "no name yet"} · edited {when(draft.updated_at)}
                        </span>
                      </button>
                      {draft.id === draftId && <span className="badge badge-sm">open</span>}
                      <button
                        className="btn btn-ghost btn-xs text-error"
                        onClick={() => void removeDraft(draft.id)}
                        aria-label={`Delete draft ${draft.title || draft.id}`}
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide opacity-70 mb-2">
              Fields you can query
            </h3>
            {cubes === null ? (
              <p className="text-xs opacity-60">
                The semantic layer's field list is unavailable right now.
              </p>
            ) : (
              <FieldReference cubes={cubes} />
            )}
          </div>
        </section>

        <section className="space-y-3">
          <div className="lg:sticky lg:top-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Preview</h2>
              {preview && (
                <span className="text-xs opacity-60">
                  {preview.row_count} rows
                  {preview.truncated && " — truncated"}
                </span>
              )}
            </div>

            {renderError && (
              <div className="alert alert-error text-sm">
                <span className="whitespace-pre-wrap">{renderError}</span>
              </div>
            )}

            {published && (
              <div className="alert alert-success text-sm">
                <span>
                  {published.created ? "Published" : "Republished"} as{" "}
                  <Link className="link font-semibold" to={published.url}>
                    {published.title}
                  </Link>
                  {published.topics.length > 0 && <> · filed under {published.topics.join(", ")}</>}
                </span>
              </div>
            )}

            {preview ? (
              <DashboardEmbed html={preview.html} title={preview.title} />
            ) : (
              <div className="card bg-base-200 border border-dashed border-base-300">
                <div className="card-body items-center text-center py-16">
                  <p className="opacity-70">
                    {rendering
                      ? "Drawing…"
                      : signedIn
                        ? "Press Render to draw your chart."
                        : "Sign in to draw your spec."}
                  </p>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>

      {mine.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Dashboards you've published</h2>
          <p className="text-sm opacity-70">
            Public, and ranked by readers' votes like every other dashboard. Load one back to
            correct it — publishing the same name again replaces it and keeps its votes.
          </p>
          <ul className="divide-y divide-base-300">
            {mine.map((dashboard) => (
              <li key={dashboard.id} className="flex items-center gap-3 py-3 flex-wrap">
                <div className="flex-1 min-w-60">
                  <Link
                    to={`/dashboards/${encodeURIComponent(dashboard.name)}`}
                    className="font-semibold hover:text-primary"
                  >
                    {dashboard.title}
                  </Link>
                  <span className="block text-xs opacity-60 font-mono">{dashboard.name}</span>
                </div>
                <span className="text-xs opacity-70">score {dashboard.score}</span>
                {dashboard.hidden && (
                  <span
                    className="badge badge-warning badge-sm"
                    title="Voted down far enough to drop off the public page — fix it and publish again"
                  >
                    hidden by votes
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
                {dashboard.spec_yaml && (
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() => loadInto(dashboard.spec_yaml ?? "", null)}
                  >
                    Load to edit
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
