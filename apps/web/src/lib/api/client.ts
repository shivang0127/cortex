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
