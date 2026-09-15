import { HealthCard } from "@/components/HealthCard";
import { API_URL } from "@/lib/api/client";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-muted">
          Live status of the three moving parts: this frontend, the FastAPI backend at{" "}
          <code className="font-mono">{API_URL}</code>, and PostgreSQL + pgvector behind it.
        </p>
      </div>

      <HealthCard />

      <p className="text-xs text-muted">
        Import your sources in the Library. Search, ask, graph, insights and review arrive in
        later phases — see ARCHITECTURE.md §10.
      </p>
    </div>
  );
}
