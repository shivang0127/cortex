import { Library } from "@/components/Library";

export default function LibraryPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Library</h1>
        <p className="mt-1 text-sm text-muted">
          Import PDFs, Markdown, text, DOCX, web pages and YouTube transcripts. Each import is
          hashed, copied into managed storage, parsed and chunked by the worker.
        </p>
      </div>
      <Library />
    </div>
  );
}
