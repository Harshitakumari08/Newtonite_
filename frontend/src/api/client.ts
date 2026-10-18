import type {
  ActivityEvent,
  ActivityPage,
  DashboardCounts,
  HealthResponse,
  Item,
  ItemCreate,
  ItemFilters,
  ItemPage,
  ItemSummary,
  ItemUpdate,
  Me,
  Member,
  Notification,
  TransitionAction,
} from "./types";

const envUrl =
  typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL
    ? String(import.meta.env.VITE_API_URL).trim().replace(/\/+$/, "")
    : "";
const BASE = envUrl ? (envUrl.endsWith("/api") ? envUrl : `${envUrl}/api`) : "/api";
const TOKEN_KEY = "ops.token";

/** Every non-2xx response becomes one of these, carrying the backend's {error: {code, message, details}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** For version_conflict and already_claimed the server sends the item as it is now. */
  get currentItem(): Item | undefined {
    return this.details.current as Item | undefined;
  }
}

/** The request never got an HTTP response (offline, connection dropped, proxy timeout). Safe to retry
 * because every mutation carries an Idempotency-Key. */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Couldn't reach the server. Check your connection and try again.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | string[] | number | undefined | null>;
  idempotencyKey?: string;
  version?: number;
}

function buildQuery(query: RequestOptions["query"]): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    for (const v of Array.isArray(value) ? value : [value]) params.append(key, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  if (opts.version !== undefined) headers["If-Match"] = String(opts.version);

  let res: Response;
  try {
    res = await fetch(BASE + path + buildQuery(opts.query), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (err) {
    throw new NetworkError(err);
  }

  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error;
    const error = new ApiError(res.status, e?.code ?? "http_error", e?.message ?? res.statusText, e?.details ?? {});
    if (res.status === 401 && path !== "/auth/login") onUnauthorized();
    throw error;
  }
  return data as T;
}

/** A user action that changes data. The key is created once per click and reused by retries. */
export interface Mutation {
  key: string;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/auth/login", { method: "POST", body: { email, password } }),
  me: () => request<Me>("/me"),
  members: (teamId: number) => request<Member[]>(`/teams/${teamId}/members`),
  dashboard: () => request<DashboardCounts>("/dashboard"),
  items: (filters: ItemFilters, cursor?: string | null, limit = 25) =>
    request<ItemPage>("/items", { query: { ...filters, cursor, limit } }),
  item: (id: number) => request<Item>(`/items/${id}`),
  similar: (id: number) => request<ItemSummary[]>(`/items/${id}/similar`),
  activity: (id: number, cursor?: string | null) =>
    request<ActivityPage>(`/items/${id}/activity`, { query: { cursor } }),
  health: () => request<HealthResponse>("/health"),
  notifications: () => request<Notification[]>("/notifications"),
  markRead: (id: number) => request<void>(`/notifications/${id}/read`, { method: "POST" }),
  markAllRead: () => request<void>("/notifications/read-all", { method: "POST" }),

  createItem: (m: Mutation & { data: ItemCreate }) =>
    request<Item>("/items", { method: "POST", body: m.data, idempotencyKey: m.key }),
  updateItem: (m: Mutation & { id: number; version: number; data: ItemUpdate }) =>
    request<Item>(`/items/${m.id}`, { method: "PATCH", body: m.data, version: m.version, idempotencyKey: m.key }),
  claim: (m: Mutation & { id: number }) =>
    request<Item>(`/items/${m.id}/claim`, { method: "POST", idempotencyKey: m.key }),
  assign: (m: Mutation & { id: number; version: number; assigneeId: number }) =>
    request<Item>(`/items/${m.id}/assign`, {
      method: "POST",
      body: { assignee_id: m.assigneeId },
      version: m.version,
      idempotencyKey: m.key,
    }),
  transition: (m: Mutation & { id: number; version: number; action: TransitionAction; comment?: string }) =>
    request<Item>(`/items/${m.id}/transitions`, {
      method: "POST",
      body: { action: m.action, comment: m.comment || null },
      version: m.version,
      idempotencyKey: m.key,
    }),
  comment: (m: Mutation & { id: number; body: string }) =>
    request<ActivityEvent>(`/items/${m.id}/comments`, {
      method: "POST",
      body: { body: m.body },
      idempotencyKey: m.key,
    }),
};
