"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ImportForm } from "@/components/ImportForm";
import { KIND_LABEL, StatusBadge } from "@/components/StatusBadge";
import { api, type Document, type Subject } from "@/lib/api/client";

const POLL_MS = 3000;
const SEARCH_DEBOUNCE_MS = 250;

type Filters = { query: string; subjectId: string; week: string; status: string };

async function fetchLibrary(filters: Filters): Promise<{ documents: Document[]; subjects: Subject[] }> {
  const [docs, subs] = await Promise.all([
    api.GET("/v1/documents", {
      params: {
        query: {
          q: filters.query.trim() || undefined,
          subject_id: filters.subjectId || undefined,
          week: filters.week === "" ? undefined : Number(filters.week),
          status: (filters.status || undefined) as Document["status"] | undefined,
          limit: 200,
        },
      },
    }),
    api.GET("/v1/subjects"),
  ]);
  return { documents: docs.data?.items ?? [], subjects: subs.data ?? [] };
}

export function Library() {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [filters, setFilters] = useState<Filters>({ query: "", subjectId: "", week: "", status: "" });
  const [search, setSearch] = useState(""); // what the box shows; `filters.query` follows after a pause
  const [pendingDelete, setPendingDelete] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Debounce typing so each keystroke doesn't hit the API; the request itself is a
  // title/filename ILIKE on the server, so results stay consistent with the other filters.
  useEffect(() => {
    const timer = setTimeout(() => setFilters((f) => (f.query === search ? f : { ...f, query: search })), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const refresh = useCallback(() => {
    fetchLibrary(filters)
      .then((r) => {
        setDocuments(r.documents);
        setSubjects(r.subjects);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [filters]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while anything is still being processed so status updates appear live.
  const busy = documents?.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      const { response } = await api.DELETE("/v1/documents/{document_id}", {
        params: { path: { document_id: pendingDelete.id } },
      });
      if (!response.ok && response.status !== 404) {
        setError(`Delete failed: HTTP ${response.status}`);
      }
      setPendingDelete(null);
      refresh();
    } finally {
      setDeleting(false);
    }
  };

  const filtersActive = Boolean(filters.query.trim() || filters.subjectId || filters.week || filters.status);

  return (
    <div className="space-y-6">
      <ImportForm subjects={subjects} onImported={refresh} />

      <section className="rounded-lg border border-border bg-panel">
        <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
          <h2 className="font-medium">Documents</h2>
          <span className="text-xs text-muted">{documents ? `${documents.length} shown` : ""}</span>
          <div className="ml-auto flex flex-wrap gap-2 text-xs">
            <label className="relative flex items-center">
              <span className="sr-only">Find a document by title</span>
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="8.5" cy="8.5" r="5.5" />
                <path d="m13 13 4.5 4.5" strokeLinecap="round" />
              </svg>
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setSearch("");
                }}
                placeholder="Find by title…"
                aria-label="Find a document by title"
                className="w-52 rounded border border-border bg-background py-1 pl-7 pr-7"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  aria-label="Clear search"
                  className="absolute right-1.5 rounded px-1 text-muted hover:text-foreground"
                >
                  ×
                </button>
              )}
            </label>
            <select
              value={filters.subjectId}
              onChange={(e) => setFilters({ ...filters, subjectId: e.target.value })}
              className="rounded border border-border bg-background px-2 py-1"
            >
              <option value="">All subjects</option>
              {subjects.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.document_count ?? 0})
                </option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              placeholder="Week"
              value={filters.week}
              onChange={(e) => setFilters({ ...filters, week: e.target.value })}
              className="w-20 rounded border border-border bg-background px-2 py-1"
            />
            <select
              value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
              className="rounded border border-border bg-background px-2 py-1"
            >
              <option value="">Any status</option>
              {["pending", "processing", "ready", "failed"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={refresh}
              className="rounded border border-border px-2.5 py-1 text-muted hover:text-foreground"
            >
              Refresh
            </button>
          </div>
        </header>

        {error && <p className="px-5 py-4 text-sm text-bad">Could not load the library: {error}</p>}
        {documents === null && !error && <p className="px-5 py-4 text-sm text-muted">Loading…</p>}
        {documents?.length === 0 && (
          <p className="px-5 py-4 text-sm text-muted">
            {filtersActive ? (
              <>
                No documents match{filters.query.trim() ? ` "${filters.query.trim()}"` : ""} with the
                current filters.{" "}
                <button
                  type="button"
                  onClick={() => {
                    setSearch("");
                    setFilters({ query: "", subjectId: "", week: "", status: "" });
                  }}
                  className="underline hover:text-foreground"
                >
                  Clear search and filters
                </button>
              </>
            ) : (
              "Nothing here yet — import something above."
            )}
          </p>
        )}

        {documents && documents.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-5 py-2">Title</th>
                  <th className="px-3 py-2">Kind</th>
                  <th className="px-3 py-2">Subjects</th>
                  <th className="px-3 py-2">Week</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2 text-right">Chunks</th>
                  <th className="px-3 py-2">Added</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.id} className="border-t border-border align-top">
                    <td className="px-5 py-2">
                      <Link href={`/library/${d.id}`} className="font-medium hover:underline">
                        {d.title}
                      </Link>
                      {d.status === "failed" && d.error && (
                        <p className="mt-0.5 max-w-md break-words font-mono text-xs text-bad">{d.error}</p>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted">{KIND_LABEL[d.kind]}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {d.subjects.map((s) => (
                          <span key={s.id} className="rounded-full border border-border px-2 py-0.5 text-xs">
                            {s.name}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-muted">{d.week ?? "—"}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={d.status} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{d.chunk_count}</td>
                    <td className="px-3 py-2 text-muted">{new Date(d.created_at).toLocaleDateString()}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => setPendingDelete(d)}
                        aria-label={`Delete ${d.title}`}
                        className="text-xs text-muted hover:text-bad"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this document?"
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      >
        {pendingDelete && (
          <>
            <p>
              <span className="font-medium text-foreground">“{pendingDelete.title}”</span>
              {pendingDelete.chunk_count > 0 && (
                <> and its {pendingDelete.chunk_count} chunk{pendingDelete.chunk_count === 1 ? "" : "s"}</>
              )}{" "}
              will be removed, along with the managed copy of the original file. This cannot be
              undone; you can import the source again later.
            </p>
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}
