import type { Document } from "@/lib/api/client";

const TONES: Record<Document["status"], string> = {
  pending: "bg-warn-bg text-warn",
  processing: "bg-warn-bg text-warn",
  ready: "bg-ok-bg text-ok",
  failed: "bg-bad-bg text-bad",
};

export function StatusBadge({ status }: { status: Document["status"] }) {
  const busy = status === "pending" || status === "processing";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs ${TONES[status]}`}>
      {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {status}
    </span>
  );
}

export const KIND_LABEL: Record<Document["kind"], string> = {
  pdf: "PDF",
  markdown: "Markdown",
  text: "Text",
  docx: "DOCX",
  web: "Web",
  youtube: "YouTube",
};
