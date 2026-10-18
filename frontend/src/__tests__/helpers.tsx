import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactElement } from "react";
import { makeQueryClient } from "../api/hooks";
import { AuthProvider } from "../auth";
import type { Item } from "../api/types";

export interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

type Responder = (call: Call) => Response | Promise<Response> | "network-error";

/** Replace fetch with a router of canned responses and record every call. */
export function mockFetch(responder: Responder) {
  const calls: Call[] = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? "GET",
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const res = await responder(call);
    if (res === "network-error") throw new TypeError("Failed to fetch");
    return res;
  }) as typeof fetch;
  return calls;
}

export const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const apiError = (status: number, code: string, message: string, details = {}) =>
  json(status, { error: { code, message, details } });

export const ME = {
  id: 2,
  name: "Alice Lead",
  email: "alice@example.com",
  is_admin: false,
  memberships: [{ team: { id: 1, name: "Support" }, role: "LEAD" }],
};

export const ADMIN_ME = {
  id: 1,
  name: "Admin",
  email: "admin@example.com",
  is_admin: true,
  memberships: [
    { team: { id: 1, name: "Support" }, role: "LEAD" },
    { team: { id: 2, name: "Payments" }, role: "LEAD" },
  ],
};

export const VIEWER_ME = {
  id: 12,
  name: "Victor Viewer",
  email: "victor@example.com",
  is_admin: false,
  memberships: [{ team: { id: 1, name: "Support" }, role: "VIEWER" }],
};

export function makeItem(overrides: Partial<Item> = {}): Item {
  return {
    id: 7,
    team: { id: 1, name: "Support" },
    type: "task",
    title: "Refund not received",
    description: "Customer waited 10 days",
    priority: "P2",
    status: "OPEN",
    assignee: null,
    requires_approval: false,
    due_at: null,
    version: 3,
    created_at: "2026-10-16T10:00:00Z",
    updated_at: "2026-10-16T10:00:00Z",
    created_by: { id: 3, name: "Carol Member" },
    resolved_at: null,
    allowed_actions: ["comment", "claim", "edit", "change_priority", "assign", "cancel"],
    ...overrides,
  };
}

export function renderAt(path: string, routes: ReactElement) {
  localStorage.setItem("ops.token", "test-token");
  const client = makeQueryClient();
  client.setDefaultOptions({
    queries: { ...client.getDefaultOptions().queries, retry: false, refetchInterval: false },
    mutations: { ...client.getDefaultOptions().mutations, retryDelay: 1 },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider>
          <Routes>{routes}</Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export { Route };
