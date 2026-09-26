import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import {
  createApiKey,
  fetchApiKeys,
  fetchApiUsage,
  revokeApiKey,
  type ApiKey,
  type ApiUsage,
  type CreatedApiKey,
} from "../lib/api";

const CATALOG_MCP_URL = "https://catalog.open-health-data-platform.org/mcp";

function formatDate(iso: string | null): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function UsageBar({ label, used, allowance }: { label: string; used: number; allowance: number }) {
  const exhausted = allowance > 0 && used >= allowance;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className={`tabular-nums ${exhausted ? "text-error" : "opacity-70"}`}>
          {used.toLocaleString()} / {allowance.toLocaleString()}
        </span>
      </div>
      <progress
        className={`progress w-full ${exhausted ? "progress-error" : "progress-primary"}`}
        value={used}
        max={Math.max(allowance, 1)}
      />
    </div>
  );
}

function Snippet({ children }: { children: string }) {
  return (
    <pre className="bg-base-200 rounded-box p-3 text-xs overflow-x-auto whitespace-pre">
      <code>{children}</code>
    </pre>
  );
}

/** The paid data API's page (`hub_api.gateway`, ADR-0030): create and revoke
 * API keys, see this month's usage against the Plus allowance, and copy the
 * endpoints. The tier shown here comes from `/api/keys/usage`, which reads
 * `users` directly. The session's `user.tier` can lag a Stripe change until
 * the next sign-in, and this page should agree with what a key gets charged as. */
export default function Developer() {
  const { user, signIn } = useAuth();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [usage, setUsage] = useState<ApiUsage | null>(null);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [nextKeys, nextUsage] = await Promise.all([fetchApiKeys(), fetchApiUsage()]);
      setKeys(nextKeys);
      setUsage(nextUsage);
    } catch (exc) {
      setError((exc as Error).message);
    }
  }, []);

  useEffect(() => {
    if (user) void reload();
  }, [user, reload]);

  const onCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      setCreated(await createApiKey(name.trim()));
      setName("");
      await reload();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (key: ApiKey) => {
    if (!window.confirm(`Revoke "${key.name || key.prefix}"? Anything using it stops working.`)) return;
    setError(null);
    try {
      await revokeApiKey(key.id);
      if (created?.id === key.id) setCreated(null);
      await reload();
    } catch (exc) {
      setError((exc as Error).message);
    }
  };

  const onCopy = async () => {
    if (!created) return;
    await navigator.clipboard.writeText(created.key);
    setCopied(true);
  };

  const base = window.location.origin;

  if (!user) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-10">
        <h1 className="text-4xl font-bold mb-4">Data API</h1>
        <p className="mb-6 opacity-80">Sign in to manage API keys.</p>
        <button className="btn btn-primary" onClick={signIn}>
          Sign in
        </button>
      </div>
    );
  }

  const isPlus = usage?.tier === "plus";

  return (
    <div className="max-w-3xl mx-auto px-4 py-10 flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-bold mb-2">Data API</h1>
        <p className="opacity-80">
          Query the semantic layer and the data catalog from your own code, notebooks and AI
          tools. Included with Plus, with a monthly allowance. Calls over the allowance are
          refused, not billed.
        </p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {usage && !isPlus && (
        <div className="alert">
          <span>
            The data API is included with Plus.{" "}
            {keys && keys.length > 0 && "Your existing keys will work again once you subscribe. "}
            <Link className="link" to="/billing">
              Upgrade to Plus
            </Link>
          </span>
        </div>
      )}

      {usage && isPlus && (
        <section className="flex flex-col gap-3">
          <h2 className="text-xl font-semibold">This month</h2>
          <UsageBar label="Cube queries" {...usage.pools.cube} />
          <UsageBar label="MCP tool calls (Cube + catalog)" {...usage.pools.mcp} />
          <p className="text-xs opacity-60">Resets {formatDate(usage.resets_at)}.</p>
        </section>
      )}

      <hr/>

      <section className="flex flex-col gap-3">
        <h2 className="text-xl font-semibold">API keys</h2>

        {created && (
          <div className="alert alert-success flex flex-col items-start gap-2">
            <span className="font-medium">
              Copy your new key now. It won&apos;t be shown again.
            </span>
            <div className="flex w-full gap-2">
              <input className="input input-sm input-bordered w-full font-mono" readOnly value={created.key} />
              <button className="btn btn-sm" onClick={onCopy}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}

        {isPlus && (
          <form className="flex gap-2" onSubmit={onCreate}>
            <input
              className="input input-bordered input-sm w-full"
              placeholder="Key name, e.g. laptop or ci"
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button className="btn btn-primary btn-sm" disabled={busy}>
              Create key
            </button>
          </form>
        )}

        {keys && keys.length === 0 && <p className="opacity-70 text-sm">No keys yet.</p>}
        {keys && keys.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Key</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id}>
                    <td>{key.name || <span className="opacity-50">unnamed</span>}</td>
                    <td className="font-mono text-xs">{key.prefix}…</td>
                    <td className="whitespace-nowrap opacity-70">{formatDate(key.created_at)}</td>
                    <td className="whitespace-nowrap opacity-70">{formatDate(key.last_used_at)}</td>
                    <td className="text-right">
                      <button className="btn btn-ghost btn-xs text-error" onClick={() => onRevoke(key)}>
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-xl font-semibold">Connect</h2>
        <p className="text-sm opacity-80">
          Every call sends your key as <code>Authorization: Bearer ohdp_…</code>.
        </p>

        <h3 className="font-semibold">MCP (Claude, Cursor, and other AI tools)</h3>
        <p className="text-sm opacity-80">
          Two servers: the semantic layer, which runs metric queries, and the catalog, which searches
          datasets, lineage and definitions. Only tool calls count toward the allowance.
        </p>
        <Snippet>{`claude mcp add --transport http ohdp-cube ${base}/v1/mcp/cube \\
  --header "Authorization: Bearer $OHDP_API_KEY"
claude mcp add --transport http ohdp-catalog ${CATALOG_MCP_URL} \\
  --header "Authorization: Bearer $OHDP_API_KEY"`}</Snippet>

        <h3 className="font-semibold">REST</h3>
        <p className="text-sm opacity-80">
          <code>GET /v1/cube/meta</code> lists every cube, measure and dimension and is free.{" "}
          <code>POST /v1/cube/load</code> runs a query, and <code>POST /v1/cube/sql</code> shows the
          SQL it compiles to. Each counts as one Cube query.
        </p>
        <Snippet>{`curl ${base}/v1/cube/load \\
  -H "Authorization: Bearer $OHDP_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"measures": ["<cube>.<measure>"], "dimensions": ["<cube>.<dimension>"], "limit": 100}'`}</Snippet>
      </section>
    </div>
  );
}
