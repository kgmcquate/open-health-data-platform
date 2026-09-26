import { fetchAdminUsers, type AdminUser } from "../lib/api";
import { useFetch } from "../lib/useFetch";

/** One `used / allowed` cell, red once the cap is hit — same convention as
 * `Navbar`'s own `UsageRow` for a signed-in user's own quota. */
function UsageCell({ used, allowed }: { used: number; allowed: number }) {
  const exhausted = used >= allowed;
  return (
    <span className={`tabular-nums whitespace-nowrap ${exhausted ? "text-error" : ""}`}>
      {used.toLocaleString()} / {allowed.toLocaleString()}
    </span>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Admin-only: every signed-up user, their tier, and what they've used today
 * against it. Reachable at `/admin`; `Navbar` only links here for an admin,
 * and the route itself renders whether or not the visitor is one — the real
 * wall is server-side (`hub_api.admin`, `auth.require_admin`), so a non-admin
 * who lands here directly just sees the 403 the API already returns. */
export default function Admin() {
  const { data, loading, error } = useFetch<AdminUser[]>(fetchAdminUsers);

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-bold mb-2">Admin</h1>
      <p className="opacity-70 mb-6">Every signed-up user, their tier, and today's usage.</p>

      {loading && <span className="loading loading-spinner loading-lg" />}
      {error && <div className="alert alert-error">{error}</div>}
      {data && data.length === 0 && <div className="alert">No users yet.</div>}

      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table table-zebra">
            <thead>
              <tr>
                <th>User</th>
                <th>Tier</th>
                <th>Questions</th>
                <th>Tokens</th>
                <th>Renders</th>
                <th>Saves</th>
                <th>Issues</th>
                <th title="Data API, this month">API: Cube</th>
                <th title="Data API, this month">API: MCP</th>
                <th>Joined</th>
                <th>Last login</th>
              </tr>
            </thead>
            <tbody>
              {data.map((user) => (
                <tr key={user.email}>
                  <td>
                    <div className="flex flex-col">
                      <span className="font-medium">{user.name || user.email}</span>
                      {user.name && <span className="text-xs opacity-70">{user.email}</span>}
                    </div>
                  </td>
                  <td>
                    {user.tier === "plus" ? (
                      <span className="badge badge-accent badge-sm">plus</span>
                    ) : (
                      <span className="badge badge-outline badge-sm">free</span>
                    )}
                  </td>
                  <td>
                    <UsageCell used={user.questions_used_today} allowed={user.questions_allowed_per_day} />
                  </td>
                  <td>
                    <UsageCell used={user.tokens_used_today} allowed={user.tokens_allowed_per_day} />
                  </td>
                  <td>
                    <UsageCell used={user.renders_used_today} allowed={user.renders_allowed_per_day} />
                  </td>
                  <td>
                    <UsageCell used={user.saves_used_today} allowed={user.saves_allowed_per_day} />
                  </td>
                  <td>
                    <UsageCell used={user.issues_used_today} allowed={user.issues_allowed_per_day} />
                  </td>
                  <td>
                    <UsageCell used={user.api_cube_used_this_month} allowed={user.api_cube_allowed_per_month} />
                  </td>
                  <td>
                    <UsageCell used={user.api_mcp_used_this_month} allowed={user.api_mcp_allowed_per_month} />
                  </td>
                  <td className="whitespace-nowrap opacity-70">{formatDate(user.created_at)}</td>
                  <td className="whitespace-nowrap opacity-70">{formatDate(user.last_login_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
