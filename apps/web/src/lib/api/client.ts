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
