/** Thin fetch wrappers for hub-api. All same-origin; the session cookie does
 * the talking (no tokens in JS). */

export interface User {
  email: string;
  name: string;
  tier: string;
}

export interface NewsItem {
  id: number;
  title: string;
  summary: string;
  url: string;
  kind: "study" | "visualization" | "platform" | string;
  published_at: string;
}

export interface DataSource {
  id: string;
  name: string;
  description: string;
  catalog_url: string;
}

export interface CuratedPlot {
  id: number;
  title: string;
  description: string;
  vega: Record<string, unknown>;
  cube_query: Record<string, unknown>;
  featured: boolean;
  created_at: string;
}

export interface LiteratureItem {
  id: number;
  title: string;
  authors: string;
  venue: string;
  year: number | null;
  url: string;
  doi: string;
  summary: string;
  tags: string[];
  trending: boolean;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${path} failed: ${response.status}`);
  return (await response.json()) as T;
}

/** Current user, or null when signed out. 401 is a state, not an error. */
export async function fetchMe(): Promise<User | null> {
  const response = await fetch("/auth/me", { credentials: "same-origin" });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`/auth/me failed: ${response.status}`);
  return (await response.json()) as User;
}

export const fetchNews = () => getJson<NewsItem[]>("/api/news");
export const fetchDataSources = () => getJson<DataSource[]>("/api/data-sources");
export const fetchPlots = () => getJson<CuratedPlot[]>("/api/plots");
export const fetchLiterature = () => getJson<LiteratureItem[]>("/api/literature");

export interface ChatModel {
  id: string;
  label: string;
  default: boolean;
}

/** The models explicitly configured in config/models.yaml, for the picker.
 * Auto-discovered models are usable by id but omitted from the picker. */
export const fetchModels = () => getJson<ChatModel[]>("/api/models");

export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
}

export interface ThreadSummary {
  id: number;
  title: string;
  status: "regular" | "archived";
  created_at: string;
  updated_at: string;
}

/** One call+result pair from `ohdp_agent.loop`'s `turn.tool_calls` — `result`/
 * `is_error`/`format` are absent for a call whose result never arrived (the
 * turn was cancelled or errored mid-call). */
export interface ThreadTurnToolCall {
  tool_call_id: string;
  name: string;
  input: Record<string, unknown>;
  result?: unknown;
  is_error?: boolean;
  format?: "html";
}

/** One entry in a turn's chronological order — a text segment or a pointer
 * (by `tool_call_id`) into that turn's own `tool_calls` — recorded live by
 * `ohdp_agent.loop.Turn.timeline`. `null`/absent on a turn logged before this
 * column existed; `runtime.ts`'s `turnToMessages` falls back to grouping
 * tool calls before the answer text for those. */
export type ThreadTurnTimelineEntry =
  | { type: "text"; text: string }
  | { type: "tool_call"; tool_call_id: string };

export interface ThreadTurn {
  id: number;
  question: string;
  answer: string;
  plan: string;
  error: string | null;
  feedback: "positive" | "negative" | null;
  tool_calls: ThreadTurnToolCall[];
  timeline: ThreadTurnTimelineEntry[] | null;
}

export interface ThreadDetail extends ThreadSummary {
  turns: ThreadTurn[];
}

async function sendJson<T>(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path} failed: ${response.status}`);
  return (await response.json()) as T;
}

export const fetchThreads = () => getJson<ThreadSummary[]>("/api/threads");
export const fetchThread = (id: number) => getJson<ThreadDetail>(`/api/threads/${id}`);
export const createThread = (title = "") =>
  sendJson<ThreadDetail>("/api/threads", "POST", { title });
export const renameThread = (id: number, title: string) =>
  sendJson<ThreadDetail>(`/api/threads/${id}`, "PATCH", { title });
export const archiveThread = (id: number) =>
  sendJson<ThreadDetail>(`/api/threads/${id}/archive`, "POST");
export const unarchiveThread = (id: number) =>
  sendJson<ThreadDetail>(`/api/threads/${id}/unarchive`, "POST");
export const deleteThread = (id: number) => sendJson<{ ok: true }>(`/api/threads/${id}`, "DELETE");

export const submitFeedback = (turnId: number, rating: "positive" | "negative") =>
  sendJson<{ ok: true }>("/api/feedback", "POST", { turn_id: turnId, rating });
