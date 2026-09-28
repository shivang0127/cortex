"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { KIND_LABEL, StatusBadge } from "@/components/StatusBadge";
import { api, type Chunk, type DocumentDetail } from "@/lib/api/client";

const POLL_MS = 3000;

type Level = "all" | "sections" | "retrieval";

async function fetchDocument(documentId: string, level: Level) {
  const [doc, ch] = await Promise.all([
    api.GET("/v1/documents/{document_id}", { params: { path: { document_id: documentId } } }),
    api.GET("/v1/documents/{document_id}/chunks", {
      params: { path: { document_id: documentId }, query: { level, limit: 1000 } },
    }),
  ]);
  if (!doc.data) {
    throw new Error(doc.response.status === 404 ? "document not found" : `HTTP ${doc.response.status}`);
  }
  return { document: doc.data, chunks: ch.data?.items ?? [] };
}

export function DocumentView({ documentId }: { documentId: string }) {
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [level, setLevel] = useState<Level>("all");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetchDocument(documentId, level).then(
        (r) => {
          setDocument(r.document);
          setChunks(r.chunks);
          setError(null);
        },
        (err) => setError(err instanceof Error ? err.message : String(err)),
      ),
    [documentId, level],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const busy = document?.status === "pending" || document?.status === "processing";
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [busy, load]);

  const reprocess = async () => {
    await api.POST("/v1/documents/{document_id}/reprocess", {
      params: { path: { document_id: documentId } },
    });
    await load();
  };

  if (error) {
    return (
      <p className="text-sm text-bad">
        {error} — <Link href="/library" className="underline">back to the library</Link>
      </p>
    );
  }
  if (!document) return <p className="text-sm text-muted">Loading…</p>;

  const meta = document.meta as Record<string, unknown>;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/library" className="text-xs text-muted hover:underline">
          ← Library
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{document.title}</h1>
          <StatusBadge status={document.status} />
        </div>
        {document.error && (
          <p className="mt-2 break-words rounded bg-bad-bg px-3 py-2 font-mono text-xs text-bad">{document.error}</p>
        )}
      </div>

      <section className="grid gap-x-8 gap-y-3 rounded-lg border border-border bg-panel px-5 py-4 text-sm sm:grid-cols-2">
        <Field label="Kind" value={KIND_LABEL[document.kind]} />
        <Field label="Week" value={document.week?.toString() ?? "—"} />
        <Field label="Subjects" value={document.subjects.map((s) => s.name).join(", ") || "—"} />
        <Field label="Classified by" value={document.classified_by} />
        <Field label="Origin" value={document.origin_uri ?? "—"} mono />
        <Field label="Managed copy" value={document.storage_path ?? "— (URL source)"} mono />
        <Field label="Content hash" value={document.content_hash.slice(0, 16) + "…"} mono />
        <Field label="Retrieval chunks" value={String(document.chunk_count)} />
        <Field
          label="Indexed for search"
          value={
            document.chunk_count === 0
              ? "—"
              : `${document.embedded_chunk_count}/${document.chunk_count} chunks embedded`
          }
        />
        {"pages" in meta && <Field label="Pages" value={String(meta.pages)} />}
        {"duration" in meta && <Field label="Duration" value={String(meta.duration)} />}
        {"author" in meta && <Field label="Author" value={String(meta.author)} />}
        {"char_count" in meta && <Field label="Characters" value={String(meta.char_count)} />}
        <Field label="Added" value={new Date(document.created_at).toLocaleString()} />
        <Field label="Updated" value={new Date(document.updated_at).toLocaleString()} />
      </section>

      <section className="rounded-lg border border-border bg-panel">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="font-medium">Jobs</h2>
          <button
            type="button"
            onClick={reprocess}
            disabled={busy}
            className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:text-foreground disabled:opacity-50"
          >
            Reprocess
          </button>
        </header>
        <ul className="divide-y divide-border text-sm">
          {document.jobs.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-3 px-5 py-2">
              <span className="font-mono text-xs">{j.type}</span>
              <span className="text-muted">{j.status}</span>
              <span className="text-xs text-muted">attempt {j.attempts}/{j.max_attempts}</span>
              <span className="ml-auto text-xs text-muted">{new Date(j.created_at).toLocaleTimeString()}</span>
              {j.last_error && (
                <p className="w-full break-words font-mono text-xs text-bad">
                  {j.last_error.split("\n").filter(Boolean).slice(-1)[0]}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-lg border border-border bg-panel">
        <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
          <h2 className="font-medium">Chunks</h2>
          <span className="text-xs text-muted">{chunks.length} shown</span>
          <div className="ml-auto flex gap-1 rounded border border-border p-0.5 text-xs">
            {(["all", "sections", "retrieval"] as const).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLevel(l)}
                className={`rounded px-2.5 py-1 ${level === l ? "bg-background font-medium" : "text-muted"}`}
              >
                {l}
              </button>
            ))}
          </div>
        </header>
        {chunks.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted">
            {busy ? "Waiting for the worker…" : "No chunks."}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {chunks.map((c) => (
              <li key={c.id} className="px-5 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="font-mono">#{c.ordinal}</span>
                  <span className={`rounded px-1.5 py-0.5 ${c.parent_id ? "bg-background" : "bg-ok-bg text-ok"}`}>
                    {c.parent_id ? "retrieval" : "section"}
                  </span>
                  <span>{c.heading_path.length ? c.heading_path.join(" › ") : "(no heading)"}</span>
                  <span className="ml-auto font-mono">
                    chars {c.char_start}–{c.char_end} · ~{c.token_count} tok
                    {c.page_start ? ` · p.${c.page_start}${c.page_end !== c.page_start ? `–${c.page_end}` : ""}` : ""}
                  </span>
                </div>
                <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm">{c.text}</pre>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className={`mt-0.5 break-words ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}
