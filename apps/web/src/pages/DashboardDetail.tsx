import { useNavigate, useParams } from "react-router-dom";
import { fetchDashboard, type Dashboard } from "../lib/api";
import { useFetch } from "../lib/useFetch";
import { DashboardCard } from "./Dashboards";

export default function DashboardDetail() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const dashboard = useFetch<Dashboard>(() => fetchDashboard(name), [name]);

  if (dashboard.loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-24 text-center">
        <span className="loading loading-spinner loading-lg" />
      </div>
    );
  }

  if (dashboard.error || !dashboard.data) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-10">
        <div className="alert alert-error">
          {dashboard.error ?? "That dashboard couldn't be found."}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <DashboardCard dashboard={dashboard.data} onDeleted={() => navigate("/dashboards")} />
    </div>
  );
}
