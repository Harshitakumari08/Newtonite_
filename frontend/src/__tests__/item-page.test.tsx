import { fireEvent, screen, waitFor } from "@testing-library/react";
import ItemPage from "../pages/ItemPage";
import { apiError, json, makeItem, ME, mockFetch, renderAt, Route, type Call } from "./helpers";

const routes = <Route path="/items/:id" element={<ItemPage />} />;

function backend(handlers: Partial<Record<string, (call: Call) => Response | "network-error">>) {
  return mockFetch((call) => {
    const path = call.url.replace(/^\/api/, "").split("?")[0];
    const handler = handlers[`${call.method} ${path}`];
    if (handler) return handler(call);
    if (path === "/me") return json(200, ME);
    if (path === "/notifications") return json(200, []);
    if (path.endsWith("/activity")) return json(200, { events: [], next_cursor: null });
    if (path.endsWith("/members")) return json(200, []);
    return apiError(404, "not_found", "Item not found");
  });
}

test("an item outside the user's teams shows not found", async () => {
  backend({});
  renderAt("/items/999", routes);
  expect(await screen.findByText("Item not found")).toBeInTheDocument();
});

test("a stale transition shows a conflict notice and the server's current item", async () => {
  const theirs = makeItem({ status: "IN_PROGRESS", version: 4, assignee: { id: 5, name: "Bob" }, allowed_actions: ["comment", "cancel"] });
  backend({
    "GET /items/7": () => json(200, makeItem()),
    "POST /items/7/transitions": () => apiError(409, "version_conflict", "changed", { current: theirs }),
  });
  window.confirm = () => true;
  renderAt("/items/7", routes);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel item" }));
  expect(await screen.findByText(/Someone else changed this item/)).toBeInTheDocument();
  expect(screen.getByText("v4")).toBeInTheDocument();
  expect(screen.getByText("Bob")).toBeInTheDocument();
});

test("claim is optimistic and rolls back to show who won", async () => {
  let release!: (r: Response) => void;
  const winner = makeItem({ status: "IN_PROGRESS", version: 4, assignee: { id: 9, name: "Dana" }, allowed_actions: ["comment"] });
  backend({
    "GET /items/7": () => json(200, makeItem()),
    "POST /items/7/claim": () => new Promise<Response>((r) => (release = r)) as unknown as Response,
  });
  renderAt("/items/7", routes);
  fireEvent.click(await screen.findByRole("button", { name: "Claim" }));
  expect(await screen.findByText("Claiming…")).toBeInTheDocument();
  expect(screen.getByText("Alice Lead", { selector: "dd" })).toBeInTheDocument();

  release(apiError(409, "already_claimed", "Dana already owns this item", { current: winner }));
  expect(await screen.findByText("Dana claimed this item first.")).toBeInTheDocument();
  expect(screen.getByText("Dana", { selector: "dd" })).toBeInTheDocument();
});

test("a retried mutation reuses its Idempotency-Key; a new click gets a new one", async () => {
  let attempts = 0;
  const calls = backend({
    "GET /items/7": () => json(200, makeItem()),
    "POST /items/7/claim": () => (++attempts === 1 ? "network-error" : json(200, makeItem({ status: "IN_PROGRESS" }))),
  });
  renderAt("/items/7", routes);
  fireEvent.click(await screen.findByRole("button", { name: "Claim" }));
  await waitFor(() => expect(attempts).toBe(2));
  const claims = calls.filter((c) => c.url.endsWith("/claim"));
  expect(claims[0].headers["Idempotency-Key"]).toBeTruthy();
  expect(claims[1].headers["Idempotency-Key"]).toBe(claims[0].headers["Idempotency-Key"]);
});

test("reject asks for a reason before calling the server", async () => {
  const calls = backend({
    "GET /items/7": () =>
      json(200, makeItem({ status: "AWAITING_APPROVAL", assignee: { id: 5, name: "Bob" }, allowed_actions: ["comment", "approve", "reject"] })),
    "POST /items/7/transitions": () => json(200, makeItem({ status: "IN_PROGRESS" })),
  });
  renderAt("/items/7", routes);
  fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
  expect(calls.some((c) => c.method === "POST")).toBe(false);
  fireEvent.change(screen.getByLabelText(/required to reject/), { target: { value: "Wrong amount" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Reject" }).at(-1)!);
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ action: "reject", comment: "Wrong amount" }));
});

test("a stale edit keeps the draft and can be re-applied on the latest version", async () => {
  const theirs = makeItem({ version: 4, description: "Carol's description" });
  const patches: Call[] = [];
  backend({
    "GET /items/7": () => json(200, makeItem()),
    "PATCH /items/7": (call) => {
      patches.push(call);
      return patches.length === 1
        ? apiError(409, "version_conflict", "changed", { current: theirs })
        : json(200, makeItem({ version: 5, title: "My title", description: "Carol's description" }));
    },
  });
  renderAt("/items/7", routes);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "My title" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/They changed the description/)).toBeInTheDocument();
  expect(screen.getByLabelText("Title")).toHaveValue("My title");

  fireEvent.click(screen.getByRole("button", { name: /Re-apply my changes/ }));
  expect(screen.getByLabelText("Description")).toHaveValue("Carol's description");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(patches).toHaveLength(2));
  expect(patches[0].headers["If-Match"]).toBe("3");
  expect(patches[1].headers["If-Match"]).toBe("4");
  expect(patches[1].body).toEqual({ title: "My title" });
});

test("similar items link to their own pages", async () => {
  backend({
    "GET /items/7": () => json(200, makeItem()),
    "GET /items/7/similar": () => json(200, [makeItem({ id: 12, title: "Refund delayed", status: "RESOLVED" })]),
  });
  renderAt("/items/7", routes);
  const link = await screen.findByRole("link", { name: /Refund delayed/ });
  expect(link).toHaveAttribute("href", "/items/12");
});

test("the similar items panel is hidden when nothing matches", async () => {
  backend({ "GET /items/7": () => json(200, makeItem()), "GET /items/7/similar": () => json(200, []) });
  renderAt("/items/7", routes);
  await screen.findByRole("button", { name: "Claim" });
  expect(screen.queryByText("Similar items")).not.toBeInTheDocument();
});
