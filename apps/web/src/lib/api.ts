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
  id: number;
  name: string;
  provider: string;
  description: string;
  homepage_url: string;
  catalog_url: string;
  tags: string[];
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

/** The built-in Claude model plus whatever OHDP_OPENAI_API_BASE_URLS/_KEYS
 * discovered at startup (hub_api.models). Always has at least one entry. */
export const fetchModels = () => getJson<ChatModel[]>("/api/models");

export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
}
