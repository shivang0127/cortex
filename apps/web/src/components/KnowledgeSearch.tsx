"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { KIND_LABEL } from "@/components/StatusBadge";
import { api, errorMessage, type EmbeddingStatus, type SearchHit, type SearchMode, type Subject } from "@/lib/api/client";

const STATUS_POLL_MS = 3000;

const MODE_HELP: Record<SearchMode, string> = {
  hybrid: "Meaning and keywords, fused (recommended)",
  semantic: "By meaning only — finds passages that say it differently",
  keyword: "Exact words only — PostgreSQL full-text search",
};

type ResultsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; query: string; mode: SearchMode; model: string | null; hits: SearchHit[] };

async function runSearch(q: string, mode: SearchMode, subjectId: string): Promise<ResultsState> {
  const { data, error, response } = await api.GET("/v1/search", {
    params: { query: { q, mode, limit: 20, subject_id: subjectId || undefined } },
  });
  if (!data) return { kind: "error", message: errorMessage(error, response.status) };
  return { kind: "ok", query: data.query, mode: data.mode, model: data.model ?? null, hits: data.results };
}

async function fetchStatus(): Promise<EmbeddingStatus | null> {
  const { data } = await api.GET("/v1/embeddings/status");
  return data ?? null;
}

export function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [subjectId, setSubjectId] = useState("");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [results, setResults] = useState<ResultsState>({ kind: "idle" });
  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [indexMessage, setIndexMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/v1/subjects").then((r) => setSubjects(r.data ?? []));
  }, []);

  const refreshStatus = useCallback(() => {
    void fetchStatus().then((s) => {
      if (s) setStatus(s);
    });
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // Poll while the worker is indexing so the coverage line moves.
  const indexing = (status?.jobs_queued ?? 0) + (status?.jobs_running ?? 0) > 0;
  useEffect(() => {
    if (!indexing) return;
    const timer = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [indexing, refreshStatus]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setResults({ kind: "loading" });
    void runSearch(q, mode, subjectId).then(setResults);
  };

  const indexNow = () => {
    setIndexMessage(null);
    void api.POST("/v1/embeddings/index", { body: { force: false } }).then(({ data, error, response }) => {
      if (!data) return setIndexMessage(errorMessage(error, response.status));
      setIndexMessage(
        data.enqueued === 0
          ? "Everything is already indexed."
          : `Queued ${data.enqueued} document${data.enqueued === 1 ? "" : "s"} for the worker.`,
      );
      refreshStatus();
    });
  };

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="rounded-lg border border-border bg-panel">
        <div className="flex flex-col gap-3 px-5 py-4">
          <label htmlFor="knowledge-query" className="text-xs uppercase tracking-wide text-muted">
            Ask in your own words — this searches the contents of your chunks, not titles
          </label>
          <div className="flex gap-2">
            <input
              id="knowledge-query"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. How does a neural network learn?"
              autoFocus
              className="flex-1 rounded border border-border bg-background px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={!query.trim() || results.kind === "loading"}
              className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
            >
              {results.kind === "loading" ? "Searching…" : "Search"}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <div className="flex gap-1 rounded border border-border p-0.5" role="radiogroup" aria-label="Search mode">
              {(["hybrid", "semantic", "keyword"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  title={MODE_HELP[m]}
                  onClick={() => setMode(m)}
                  className={`rounded px-2.5 py-1 ${mode === m ? "bg-background font-medium" : "text-muted"}`}
                >
                  {m}
                </button>
              ))}
            </div>
            <span className="text-muted">{MODE_HELP[mode]}</span>
            <select
              value={subjectId}
              onChange={(e) => setSubjectId(e.target.value)}
              aria-label="Limit to subject"
              className="ml-auto rounded border border-border bg-background px-2 py-1"
            >
              <option value="">All subjects</option>
              {subjects.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <footer className="flex flex-wrap items-center gap-3 border-t border-border px-5 py-2 text-xs text-muted">
          {status ? (
            status.enabled ? (
              <>
                <span>
                  Index: <span className="font-medium text-foreground">{status.chunks_embedded}</span> of{" "}
                  {status.chunks_total} chunks ·{" "}
                  {status.documents_complete}/{status.documents_total} documents · {status.model}
                  {indexing && ` · ${status.jobs_queued + status.jobs_running} job(s) running`}
                  {status.jobs_failed > 0 && ` · ${status.jobs_failed} failed`}
                </span>
                <button
                  type="button"
                  onClick={indexNow}
                  className="rounded border border-border px-2 py-0.5 hover:text-foreground"
                >
                  Index now
                </button>
                {indexMessage && <span>{indexMessage}</span>}
              </>
            ) : (
              <span>Embeddings are disabled (EMBEDDING_PROVIDER=none) — keyword mode only.</span>
            )
          ) : (
            <span>Checking index…</span>
          )}
        </footer>
      </form>

      {results.kind === "error" && <p className="text-sm text-bad">Search failed: {results.message}</p>}
      {results.kind === "ok" && results.hits.length === 0 && (
        <p className="text-sm text-muted">
          Nothing matched “{results.query}”
          {results.mode === "keyword" ? " — try hybrid mode for matches by meaning." : "."}
        </p>
      )}
      {results.kind === "ok" && results.hits.length > 0 && (
        <ol className="space-y-3">
          <li className="text-xs text-muted">
            {results.hits.length} result{results.hits.length === 1 ? "" : "s"} for “{results.query}” ({results.mode}
            {results.model ? `, ${results.model}` : ""})
          </li>
          {results.hits.map((hit, i) => (
            <li key={hit.chunk_id} className="rounded-lg border border-border bg-panel px-5 py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className="font-mono">#{i + 1}</span>
                <Link href={`/library/${hit.document_id}`} className="font-medium text-foreground hover:underline">
                  {hit.document_title}
                </Link>
                <span>{KIND_LABEL[hit.document_kind as keyof typeof KIND_LABEL] ?? hit.document_kind}</span>
                {hit.heading_path.length > 0 && <span>· {hit.heading_path.join(" › ")}</span>}
                {hit.page_start && (
                  <span>
                    · p.{hit.page_start}
                    {hit.page_end && hit.page_end !== hit.page_start ? `–${hit.page_end}` : ""}
                  </span>
                )}
                {hit.week != null && <span>· week {hit.week}</span>}
                {hit.subjects.length > 0 && <span>· {hit.subjects.join(", ")}</span>}
                <span className="ml-auto font-mono" title="chunk ordinal · character offsets">
                  chunk #{hit.ordinal} · chars {hit.char_start}–{hit.char_end}
                </span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm">{hit.text}</p>
              <div className="mt-2 flex flex-wrap gap-3 font-mono text-xs text-muted">
                <span>score {hit.score.toFixed(4)}</span>
                {hit.similarity != null && <span>similarity {hit.similarity.toFixed(3)}</span>}
                {hit.semantic_rank != null && <span>semantic #{hit.semantic_rank}</span>}
                {hit.keyword_rank != null && <span>keyword #{hit.keyword_rank}</span>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
