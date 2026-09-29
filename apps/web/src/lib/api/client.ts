/**
 * The one place the frontend talks to FastAPI.
 *
 * The browser calls the API directly — there is no Next.js route handler in
 * between (ARCHITECTURE.md §1). `paths` is generated from the backend's
 * OpenAPI schema (`npm run generate:api`), so every request and response here
 * is typed against what the server actually serves.
 */

import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const api = createClient<paths>({ baseUrl: API_URL });

export type HealthReport = components["schemas"]["HealthReport"];
export type DatabaseHealth = components["schemas"]["DatabaseHealth"];
export type Document = components["schemas"]["DocumentOut"];
export type DocumentDetail = components["schemas"]["DocumentDetailOut"];
export type Subject = components["schemas"]["SubjectOut"];
export type Chunk = components["schemas"]["ChunkOut"];
export type Job = components["schemas"]["JobOut"];
export type ImportResponse = components["schemas"]["ImportResponse"];
export type SearchHit = components["schemas"]["SearchHitOut"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type SearchMode = SearchResponse["mode"];
export type EmbeddingStatus = components["schemas"]["EmbeddingStatusOut"];
export type AskRequest = components["schemas"]["AskRequest"];
export type AskResponse = components["schemas"]["AskResponse"];
export type AskSource = components["schemas"]["SourceOut"];
export type AskCitation = components["schemas"]["CitationOut"];
export type LLMStatus = components["schemas"]["LLMStatusOut"];

export type ImportRequest = {
  file?: File;
  url?: string;
  subjects: string[];
  week?: number;
  title?: string;
};

/** Multipart import: exactly one of `file` / `url`. Subjects are repeated form fields. */
export async function importDocument(request: ImportRequest): Promise<ImportResponse> {
  const form = new FormData();
  if (request.file) form.append("file", request.file, request.file.name);
  if (request.url) form.append("url", request.url);
  for (const subject of request.subjects) form.append("subjects", subject);
  if (request.week !== undefined) form.append("week", String(request.week));
  if (request.title) form.append("title", request.title);

  const { data, error, response } = await api.POST("/v1/documents", {
    // The generated type describes the fields; the serializer turns them into FormData.
    body: {} as components["schemas"]["Body_import_document_v1_documents_post"],
    bodySerializer: () => form,
  });
  if (!data) throw new Error(errorMessage(error, response.status));
  return data;
}

/** FastAPI puts a human-readable reason in `detail` (string or validation list). */
export function errorMessage(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d))
      .join("; ");
  }
  return `HTTP ${status}`;
}

/**
 * Stream an answer from `POST /v1/ask/stream`.
 *
 * `EventSource` is GET-only, so the SSE frames are parsed off a `fetch` body
 * instead. Events arrive as `sources` → `delta`… → `result`, or `error`.
 */
export type AskStreamHandlers = {
  onSources?: (sources: AskSource[]) => void;
  onDelta?: (text: string) => void;
  onResult?: (result: AskResponse) => void;
  onError?: (message: string) => void;
};

export async function streamAsk(
  request: AskRequest,
  handlers: AskStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/v1/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = `HTTP ${response.status}`;
    try {
      detail = errorMessage(await response.json(), response.status);
    } catch {
      /* the body was not JSON; the status is all we have */
    }
    handlers.onError?.(detail);
    return;
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += value;
    // SSE frames are separated by a blank line; keep any partial frame buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (!data.length) continue;
      const payload = JSON.parse(data.join("\n"));
      if (event === "sources") handlers.onSources?.(payload as AskSource[]);
      else if (event === "delta") handlers.onDelta?.((payload as { text: string }).text);
      else if (event === "result") handlers.onResult?.(payload as AskResponse);
      else if (event === "error") {
        handlers.onError?.((payload as { detail: string }).detail);
      }
    }
  }
}
