"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { KIND_LABEL } from "@/components/StatusBadge";
import {
  api,
  streamAsk,
  type AskResponse,
  type AskSource,
  type LLMStatus,
  type SearchMode,
  type Subject,
} from "@/lib/api/client";

type Phase = "idle" | "retrieving" | "generating" | "done" | "error";

const MODE_HELP: Record<SearchMode, string> = {
  hybrid: "Meaning and keywords (recommended)",
  semantic: "Meaning only",
  keyword: "Exact words only",
};

/** Split an answer into text and citation markers so markers can be rendered as chips. */
function segments(answer: string): Array<{ text: string; marker?: number }> {
  const out: Array<{ text: string; marker?: number }> = [];
  const pattern = /\[S(\d+)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(answer)) !== null) {
    if (match.index > last) out.push({ text: answer.slice(last, match.index) });
    out.push({ text: match[0], marker: Number(match[1]) });
    last = match.index + match[0].length;
  }
  if (last < answer.length) out.push({ text: answer.slice(last) });
  return out;
}

export function AskSecondBrain() {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [subjectId, setSubjectId] = useState("");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [status, setStatus] = useState<LLMStatus | null>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [asked, setAsked] = useState("");
  const [streamed, setStreamed] = useState("");
  const [sources, setSources] = useState<AskSource[]>([]);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    void api.GET("/v1/subjects").then((r) => setSubjects(r.data ?? []));
    void api.GET("/v1/llm/status").then((r) => setStatus(r.data ?? null));
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  const submit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      const q = question.trim();
      if (!q || phase === "retrieving" || phase === "generating") return;

      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;

      setAsked(q);
      setStreamed("");
      setSources([]);
      setResult(null);
      setError(null);
      setPhase("retrieving");

      void streamAsk(
        {
          question: q,
          mode,
          subject_id: subjectId || null,
        },
        {
          onSources: (next) => {
            setSources(next);
            setPhase("generating");
          },
          onDelta: (text) => setStreamed((current) => current + text),
          onResult: (final) => {
            setResult(final);
            setPhase("done");
          },
          onError: (message) => {
            setError(message);
            setPhase("error");
          },
        },
        controller.signal,
      ).catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setPhase("error");
      });
    },
    [question, mode, subjectId, phase],
  );

  const busy = phase === "retrieving" || phase === "generating";
  const answer = result?.answer ?? streamed;
  const shown = result?.sources ?? sources;
  const cited = new Set((result?.citations ?? []).map((c) => c.marker));

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="rounded-lg border border-border bg-panel">
        <div className="flex flex-col gap-3 px-5 py-4">
          <label htmlFor="ask-question" className="text-xs uppercase tracking-wide text-muted">
            Ask a question — answered only from what you have imported
          </label>
          <div className="flex gap-2">
            <input
              id="ask-question"
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. How does a neural network learn?"
              autoFocus
              className="flex-1 rounded border border-border bg-background px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={!question.trim() || busy}
              className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
            >
              {busy ? "Thinking…" : "Ask"}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <div
              className="flex gap-1 rounded border border-border p-0.5"
              role="radiogroup"
              aria-label="Retrieval mode"
            >
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
            <span className="text-muted">retrieval: {MODE_HELP[mode]}</span>
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
        <footer className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-2 text-xs text-muted">
          {status === null ? (
            <span>Checking the model…</span>
          ) : !status.enabled ? (
            <span className="text-warn">Answering is disabled (LLM_PROVIDER=none).</span>
          ) : !status.reachable ? (
            <span className="text-bad">
              {status.provider} is not reachable — {status.detail ?? "start the runtime"}.
            </span>
          ) : !status.model_available ? (
            <span className="text-bad">
              Model {status.model} is not installed — {status.detail}.
            </span>
          ) : (
            <span>
              Answers generated locally by <span className="text-foreground">{status.model}</span>{" "}
              ({status.provider}) · {status.context_window} token context · nothing leaves this
              machine.
            </span>
          )}
        </footer>
      </form>

      {error && (
        <p className="rounded-lg border border-border bg-bad-bg px-5 py-3 text-sm text-bad">
          {error}
        </p>
      )}

      {phase === "retrieving" && (
        <p className="text-sm text-muted">Searching your library…</p>
      )}

      {(answer || busy) && phase !== "retrieving" && (
        <section
          className={`rounded-lg border bg-panel ${result?.refused ? "border-warn" : "border-border"}`}
        >
          <header className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3">
            <h2 className="font-medium">{result?.refused ? "Not in your library" : "Answer"}</h2>
            {result && !result.refused && (
              <span
                className={`rounded px-2 py-0.5 text-xs ${result.grounded ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}
                title={
                  result.grounded
                    ? "Every citation resolves to a chunk that was retrieved"
                    : "The model answered without citing any of the supplied sources — treat with care"
                }
              >
                {result.grounded ? "grounded" : "uncited"}
              </span>
            )}
            <span className="ml-auto text-xs text-muted">“{asked}”</span>
          </header>

          <div className="px-5 py-4">
            <p className="whitespace-pre-wrap text-sm leading-relaxed">
              {segments(answer).map((segment, i) =>
                segment.marker === undefined ? (
                  <span key={i}>{segment.text}</span>
                ) : (
                  <a
                    key={i}
                    href={`#source-${segment.marker}`}
                    className="mx-0.5 rounded bg-background px-1 font-mono text-xs text-muted hover:text-foreground"
                    title={`Jump to source S${segment.marker}`}
                  >
                    {segment.text}
                  </a>
                ),
              )}
              {busy && <span className="ml-0.5 animate-pulse">▌</span>}
            </p>

            {result && !result.refused && !result.grounded && (
              <p className="mt-3 rounded bg-warn-bg px-3 py-2 text-xs text-warn">
                This answer cites none of the retrieved passages, so it could not be traced back
                to your library. Check the sources below before trusting it.
              </p>
            )}
          </div>

          {result && (
            <footer className="border-t border-border px-5 py-2 font-mono text-xs text-muted">
              {result.generation.model} · {result.generation.latency_ms} ms ·{" "}
              {result.retrieval.hits} retrieved, {result.retrieval.above_floor} above the
              relevance floor ({result.retrieval.min_similarity}) · {result.retrieval.context_tokens}{" "}
              context tokens
              {result.generation.truncated && " · answer hit the length limit"}
            </footer>
          )}
        </section>
      )}

      {result?.refused && (
        <p className="text-sm text-muted">
          Nothing in your library cleared the relevance floor for this question.{" "}
          <Link href="/search" className="underline hover:text-foreground">
            Search Knowledge
          </Link>{" "}
          to see what came closest, or import a source on this topic.
        </p>
      )}

      {shown.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-medium">
            Sources given to the model{" "}
            <span className="font-normal text-muted">
              ({shown.length}
              {cited.size > 0 && `, ${cited.size} cited`})
            </span>
          </h2>
          {shown.map((source) => (
            <article
              key={source.chunk_id}
              id={`source-${source.marker}`}
              className={`rounded-lg border bg-panel px-5 py-3 scroll-mt-4 ${
                cited.has(source.marker) ? "border-ok" : "border-border"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className="rounded bg-background px-1.5 py-0.5 font-mono">
                  S{source.marker}
                </span>
                {cited.has(source.marker) && <span className="text-ok">cited</span>}
                <Link
                  href={`/library/${source.document_id}`}
                  className="font-medium text-foreground hover:underline"
                >
                  {source.document_title}
                </Link>
                <span>
                  {KIND_LABEL[source.document_kind as keyof typeof KIND_LABEL] ??
                    source.document_kind}
                </span>
                {source.heading_path.length > 0 && <span>· {source.heading_path.join(" › ")}</span>}
                {source.page_start && (
                  <span>
                    · p.{source.page_start}
                    {source.page_end && source.page_end !== source.page_start
                      ? `–${source.page_end}`
                      : ""}
                  </span>
                )}
                {source.week != null && <span>· week {source.week}</span>}
                <span className="ml-auto font-mono">
                  chunk #{source.ordinal} · chars {source.char_start}–{source.char_end}
                  {source.similarity != null && ` · sim ${source.similarity.toFixed(3)}`}
                </span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm">{source.text}</p>
              {source.truncated && (
                <p className="mt-1 text-xs text-muted">…truncated to fit the context budget</p>
              )}
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
