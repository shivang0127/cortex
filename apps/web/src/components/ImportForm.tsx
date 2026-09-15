"use client";

import { useRef, useState } from "react";
import { importDocument, type ImportResponse, type Subject } from "@/lib/api/client";

const ACCEPT = ".pdf,.md,.markdown,.txt,.text,.docx";

type Props = {
  subjects: Subject[];
  onImported: (result: ImportResponse) => void;
};

export function ImportForm({ subjects, onImported }: Props) {
  const [mode, setMode] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [subjectText, setSubjectText] = useState("");
  const [week, setWeek] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const clearFile = () => {
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = ""; // so re-picking the same file fires onChange
  };

  const subjectList = subjectText
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setMessage(null);
    if (mode === "file" && !file) return setMessage({ tone: "bad", text: "Choose a file first." });
    if (mode === "url" && !url.trim()) return setMessage({ tone: "bad", text: "Enter a URL." });
    setBusy(true);
    try {
      const result = await importDocument({
        file: mode === "file" ? (file ?? undefined) : undefined,
        url: mode === "url" ? url.trim() : undefined,
        subjects: subjectList,
        week: week === "" ? undefined : Number(week),
        title: title.trim() || undefined,
      });
      setMessage({
        tone: "ok",
        text: result.duplicate
          ? `Already in the library: "${result.document.title}" (subjects updated).`
          : `Queued "${result.document.title}" — job ${result.job?.id.slice(0, 8)}.`,
      });
      clearFile();
      setUrl("");
      setTitle("");
      onImported(result);
    } catch (err) {
      setMessage({ tone: "bad", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  const addSubject = (name: string) => {
    if (subjectList.includes(name)) return;
    setSubjectText(subjectList.concat(name).join(", "));
  };

  return (
    <form onSubmit={submit} className="rounded-lg border border-border bg-panel">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <h2 className="font-medium">Import</h2>
        <div className="flex gap-1 rounded border border-border p-0.5 text-xs">
          {(["file", "url"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`rounded px-2.5 py-1 ${mode === m ? "bg-background font-medium" : "text-muted"}`}
            >
              {m === "file" ? "Local file" : "Web / YouTube URL"}
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1 text-sm sm:col-span-2">
          <label
            htmlFor={mode === "file" ? "import-file" : "import-url"}
            className="clickable text-xs uppercase tracking-wide text-muted"
          >
            {mode === "file" ? "File (PDF, Markdown, text, DOCX)" : "URL (web page or YouTube video)"}
          </label>
          {mode === "file" ? (
            <div className="flex items-center gap-3 rounded border border-border bg-background px-2 py-1.5">
              {/* The real <input> stays in the DOM (visually hidden, still focusable) so the
                  native picker, Tab focus and Enter/Space all keep working. */}
              <label
                htmlFor="import-file"
                className="clickable inline-flex rounded border border-border bg-panel px-2.5 py-1 text-xs font-medium hover:bg-background focus-within:ring-2 focus-within:ring-foreground/40"
              >
                Choose file…
                <input
                  id="import-file"
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPT}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="sr-only"
                />
              </label>
              <span className={`truncate ${file ? "" : "text-muted"}`} aria-live="polite">
                {file ? file.name : "No file chosen"}
              </span>
              {file && (
                <button
                  type="button"
                  onClick={clearFile}
                  aria-label="Clear selected file"
                  className="ml-auto text-xs text-muted hover:text-foreground"
                >
                  Clear
                </button>
              )}
            </div>
          ) : (
            <input
              id="import-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              className="rounded border border-border bg-background px-2 py-1.5 text-sm"
            />
          )}
        </div>

        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-muted">
            Subjects (comma-separated; new names are created)
          </span>
          <input
            type="text"
            value={subjectText}
            onChange={(e) => setSubjectText(e.target.value)}
            placeholder="Theory of Computing Science, Discrete Maths"
            className="rounded border border-border bg-background px-2 py-1.5 text-sm"
          />
          {subjects.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {subjects.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => addSubject(s.name)}
                  className="rounded-full border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground"
                >
                  + {s.name}
                </button>
              ))}
            </div>
          )}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted">Week (optional)</span>
          <input
            type="number"
            min={0}
            max={60}
            value={week}
            onChange={(e) => setWeek(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted">Title override (optional)</span>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
      </div>

      <footer className="flex items-center justify-between gap-4 border-t border-border px-5 py-3">
        <p className={`text-sm ${message?.tone === "bad" ? "text-bad" : "text-muted"}`}>
          {message?.text ?? "The original is copied into Second Brain's managed storage."}
        </p>
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-foreground px-3 py-1.5 text-sm font-medium text-background disabled:opacity-50"
        >
          {busy ? "Importing…" : "Import"}
        </button>
      </footer>
    </form>
  );
}
