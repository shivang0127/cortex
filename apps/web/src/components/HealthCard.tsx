"use client";

import { useEffect, useState } from "react";
import { API_URL, api, type HealthReport } from "@/lib/api/client";

type State =
  | { kind: "loading" }
  | { kind: "ok"; report: HealthReport; fetchedAt: Date }
  | { kind: "unreachable"; message: string };

const REFRESH_MS = 10_000;

/** Pure: talks to the API and describes the outcome, never touches React state. */
async function fetchHealth(): Promise<State> {
  try {
    const { data, response } = await api.GET("/v1/health");
    if (!data) return { kind: "unreachable", message: `HTTP ${response.status}` };
    return { kind: "ok", report: data, fetchedAt: new Date() };
  } catch (err) {
    return { kind: "unreachable", message: err instanceof Error ? err.message : String(err) };
  }
}

export function HealthCard() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchHealth().then((next) => {
        if (active) setState(next);
      });
    };
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const load = () => void fetchHealth().then(setState);

  return (
    <section className="rounded-lg border border-border bg-panel">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <h2 className="font-medium">System health</h2>
        <button
          type="button"
          onClick={load}
          className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:text-foreground"
        >
          Refresh
        </button>
      </header>

      <div className="px-5 py-4">
        {state.kind === "loading" && <p className="text-sm text-muted">Contacting API…</p>}

        {state.kind === "unreachable" && (
          <Row label="API" tone="bad" value={`unreachable (${state.message})`}>
            <p className="mt-1 text-xs text-muted">
              Is the backend running? Start it with <code className="font-mono">secondbrain-api</code>{" "}
              and check that <code className="font-mono">{API_URL}</code> is the right address.
            </p>
          </Row>
        )}

        {state.kind === "ok" && <Report report={state.report} fetchedAt={state.fetchedAt} />}
      </div>
    </section>
  );
}

function Report({ report, fetchedAt }: { report: HealthReport; fetchedAt: Date }) {
  const db = report.database;
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
      <Row label="API" tone="ok" value={`${report.app} v${report.version} · ${report.environment}`} />
      <Row label="Overall" tone={report.status === "ok" ? "ok" : "warn"} value={report.status} />
      <Row
        label="PostgreSQL"
        tone={db.reachable ? "ok" : "bad"}
        value={db.reachable ? (db.server_version ?? "reachable") : "unreachable"}
      >
        {!db.reachable && db.error && (
          <p className="mt-1 break-words font-mono text-xs text-muted">{db.error}</p>
        )}
      </Row>
      <Row
        label="pgvector"
        tone={db.pgvector_version ? "ok" : "warn"}
        value={db.pgvector_version ? `v${db.pgvector_version}` : "not installed — run migrations"}
      />
      <Row
        label="Migration"
        tone={db.migration_revision ? "ok" : "warn"}
        value={db.migration_revision ?? "none applied"}
      />
      <Row label="Fetched" tone="neutral" value={fetchedAt.toLocaleTimeString()} />
    </dl>
  );
}

type Tone = "ok" | "warn" | "bad" | "neutral";

const TONE: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  neutral: "bg-background text-muted",
};

function Row({
  label,
  value,
  tone,
  children,
}: {
  label: string;
  value: string;
  tone: Tone;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1">
        <span className={`inline-block rounded px-2 py-0.5 text-sm ${TONE[tone]}`}>{value}</span>
        {children}
      </dd>
    </div>
  );
}
