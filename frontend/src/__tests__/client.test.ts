import { api, ApiError, NetworkError } from "../api/client";
import { apiError, json, makeItem, mockFetch } from "./helpers";

beforeEach(() => localStorage.setItem("ops.token", "tok"));

test("mutations send the token, If-Match and Idempotency-Key", async () => {
  const calls = mockFetch(() => json(200, makeItem()));
  await api.transition({ key: "k-1", id: 7, version: 3, action: "resolve" });
  expect(calls[0].url).toBe("/api/items/7/transitions");
  expect(calls[0].headers).toMatchObject({
    Authorization: "Bearer tok",
    "If-Match": "3",
    "Idempotency-Key": "k-1",
  });
  expect(calls[0].body).toEqual({ action: "resolve", comment: null });
});

test("list filters become repeated query params and empty ones are dropped", async () => {
  const calls = mockFetch(() => json(200, { items: [], next_cursor: null }));
  await api.items({ status: ["OPEN", "IN_PROGRESS"], priority: [], q: "", view: "assigned_to_me" }, "abc");
  expect(calls[0].url).toBe("/api/items?status=OPEN&status=IN_PROGRESS&view=assigned_to_me&cursor=abc&limit=25");
});

test("error bodies become ApiError with the server's current item", async () => {
  const current = makeItem({ version: 4 });
  mockFetch(() => apiError(409, "version_conflict", "changed", { current }));
  const err = await api.item(7).catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.code).toBe("version_conflict");
  expect(err.currentItem.version).toBe(4);
});

test("a dropped connection is a NetworkError", async () => {
  mockFetch(() => "network-error");
  await expect(api.item(7)).rejects.toBeInstanceOf(NetworkError);
});

test("api.health fetches health status from /health endpoint", async () => {
  const calls = mockFetch(() =>
    json(200, { status: "healthy", version: "0.1.0", database: "connected", service: "newtonite-api" }),
  );
  const res = await api.health();
  expect(calls[0].url).toBe("/api/health");
  expect(res.status).toBe("healthy");
  expect(res.database).toBe("connected");
});
