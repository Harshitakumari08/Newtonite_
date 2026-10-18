import { fireEvent, screen, waitFor } from "@testing-library/react";
import LoginPage from "../pages/LoginPage";
import DashboardPage from "../pages/DashboardPage";
import ItemListPage from "../pages/ItemListPage";
import NewItemPage from "../pages/NewItemPage";
import ItemPage from "../pages/ItemPage";
import NotificationsPage from "../pages/NotificationsPage";
import { ADMIN_ME, apiError, json, makeItem, ME, mockFetch, renderAt, Route, VIEWER_ME, type Call } from "./helpers";

function standardBackend(handlers: Partial<Record<string, (call: Call) => Response | "network-error">>) {
  return mockFetch((call) => {
    const path = call.url.replace(/^\/api/, "").split("?")[0];
    const handler = handlers[`${call.method} ${path}`];
    if (handler) return handler(call);
    if (path === "/me") return json(200, ME);
    if (path === "/notifications") return json(200, []);
    if (path === "/dashboard") return json(200, { assigned_to_me: 3, unassigned_urgent: 1, awaiting_my_approval: 2 });
    if (path === "/items") return json(200, { items: [makeItem()], next_cursor: null });
    if (path.endsWith("/activity")) return json(200, { events: [], next_cursor: null });
    if (path.endsWith("/members")) return json(200, []);
    return apiError(404, "not_found", "Not found");
  });
}

describe("Frontend Key User Flows", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  test("LoginPage handles credentials, errors, and demo autofill", async () => {
    const calls = mockFetch((call) => {
      const path = call.url.replace(/^\/api/, "");
      if (path === "/me") return json(200, ME);
      if (path === "/auth/login" && call.body && (call.body as { email: string }).email === "alice@example.com" && (call.body as { password: string }).password === "password") {
        return json(200, { access_token: "jwt-token-alice" });
      }
      return apiError(401, "invalid_credentials", "Email or password is incorrect");
    });

    renderAt(
      "/login",
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>Home</div>} />
      </>,
    );

    // Click demo account button
    const demoAlice = screen.getByRole("button", { name: "alice" });
    fireEvent.click(demoAlice);
    expect(screen.getByLabelText("Email")).toHaveValue("alice@example.com");
    expect(screen.getByLabelText("Password")).toHaveValue("password");

    // Submit invalid credentials first
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrongpass" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect");

    // Submit valid credentials
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => {
      expect(calls.some((c) => c.url.endsWith("/auth/login"))).toBe(true);
      expect(localStorage.getItem("ops.token")).toBe("jwt-token-alice");
    });
  });

  test("DashboardPage renders real counts from API and panel sections", async () => {
    standardBackend({
      "GET /dashboard": () => json(200, { assigned_to_me: 5, unassigned_urgent: 2, awaiting_my_approval: 4 }),
      "GET /items": () => json(200, { items: [makeItem({ id: 101, title: "Urgent outage" })], next_cursor: null }),
    });

    renderAt("/", <Route path="/" element={<DashboardPage />} />);

    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    expect(await screen.findByText("5")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getAllByText("Assigned to me").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unassigned urgent").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Awaiting my approval").length).toBeGreaterThan(0);
  });

  test("ItemListPage renders filter controls and handles empty state", async () => {
    standardBackend({
      "GET /items": () => json(200, { items: [], next_cursor: null }),
    });

    renderAt("/items", <Route path="/items" element={<ItemListPage />} />);

    expect(await screen.findByRole("searchbox", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Team" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Type" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Assignee" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Sort" })).toBeInTheDocument();

    // Verify empty state message
    expect(await screen.findByText("Nothing here.")).toBeInTheDocument();
  });

  test("NewItemPage enforces approval checkbox for payment items and creates item", async () => {
    const calls = standardBackend({
      "POST /items": (call) => json(201, makeItem({ id: 42, ...(call.body as object) })),
    });

    renderAt(
      "/items/new",
      <>
        <Route path="/items/new" element={<NewItemPage />} />
        <Route path="/items/:id" element={<div>Item Created Page</div>} />
      </>,
    );

    expect(await screen.findByText("New item")).toBeInTheDocument();

    // Wait for teams to load
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Team" })).toHaveValue("1");
    });

    // Default task does not force approval
    const approvalCheckbox = screen.getByRole("checkbox", { name: /Needs a Lead's approval/ });
    expect(approvalCheckbox).not.toBeChecked();
    expect(approvalCheckbox).not.toBeDisabled();

    // Switch type to "payment"
    const typeSelect = screen.getByLabelText("Type");
    fireEvent.change(typeSelect, { target: { value: "payment" } });

    // Payment forces approval and disables checkbox
    expect(approvalCheckbox).toBeChecked();
    expect(approvalCheckbox).toBeDisabled();
    expect(screen.getByText(/always, for Payment/)).toBeInTheDocument();

    // Fill title and description
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Vendor payment approval" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Wire $15,000 to hosting provider" } });

    // Submit form
    fireEvent.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => {
      const createCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/items"));
      expect(createCall).toBeDefined();
      expect(createCall?.body).toMatchObject({
        type: "payment",
        title: "Vendor payment approval",
        description: "Wire $15,000 to hosting provider",
        requires_approval: true,
      });
      expect(createCall?.headers["Idempotency-Key"]).toBeTruthy();
    });
  });

  test("ItemPage renders role-appropriate approval actions and executes approve", async () => {
    const calls = standardBackend({
      "GET /items/7": () =>
        json(
          200,
          makeItem({
            id: 7,
            type: "payment",
            status: "AWAITING_APPROVAL",
            requires_approval: true,
            allowed_actions: ["comment", "approve", "reject"],
          }),
        ),
      "POST /items/7/transitions": (call) => json(200, makeItem({ id: 7, status: "RESOLVED", ...(call.body as object) })),
    });

    renderAt("/items/7", <Route path="/items/:id" element={<ItemPage />} />);

    // Check approval buttons are present
    const approveBtn = await screen.findByRole("button", { name: "Approve" });
    const rejectBtn = screen.getByRole("button", { name: "Reject" });
    expect(approveBtn).toBeInTheDocument();
    expect(rejectBtn).toBeInTheDocument();

    // Click Approve
    fireEvent.click(approveBtn);

    await waitFor(() => {
      const transCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/transitions"));
      expect(transCall).toBeDefined();
      expect(transCall?.body).toEqual({ action: "approve", comment: null });
    });
  });

  test("NewItemPage allows admin to select from all teams and create item", async () => {
    const calls = standardBackend({
      "GET /me": () => json(200, ADMIN_ME),
      "POST /items": (call) => json(201, makeItem({ id: 99, ...(call.body as object) })),
    });

    renderAt(
      "/items/new",
      <>
        <Route path="/items/new" element={<NewItemPage />} />
        <Route path="/items/:id" element={<div>Item Created Page</div>} />
      </>,
    );

    expect(await screen.findByText("New item")).toBeInTheDocument();

    // Verify both teams are available in the dropdown
    const teamSelect = await screen.findByRole("combobox", { name: "Team" });
    expect(teamSelect).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Support" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Payments" })).toBeInTheDocument();

    // Select Payments team
    fireEvent.change(teamSelect, { target: { value: "2" } });
    expect(teamSelect).toHaveValue("2");

    // Viewer restriction warning is not present
    expect(screen.queryByText(/You're a viewer in all your teams/)).not.toBeInTheDocument();

    // Fill form and submit
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Admin created item" } });
    fireEvent.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => {
      const createCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/items"));
      expect(createCall).toBeDefined();
      expect(createCall?.body).toMatchObject({
        team_id: 2,
        title: "Admin created item",
      });
    });
  });

  test("NewItemPage blocks viewers with explicit viewer message", async () => {
    standardBackend({
      "GET /me": () => json(200, VIEWER_ME),
    });

    renderAt("/items/new", <Route path="/items/new" element={<NewItemPage />} />);

    expect(
      await screen.findByText("You're a viewer in all your teams, so you can't create items."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Team" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create item" })).not.toBeInTheDocument();
  });

  test("NotificationsPage renders unread count and allows marking all as read", async () => {
    const mockNotifications = [
      { id: 1, user_id: 1, item_id: 10, message: "P1 incident created", read_at: null, created_at: new Date().toISOString() },
      { id: 2, user_id: 1, item_id: 11, message: "Claimed by Alice", read_at: null, created_at: new Date().toISOString() },
    ];

    const calls = standardBackend({
      "GET /notifications": () => json(200, mockNotifications),
      "POST /notifications/read-all": () => new Response(null, { status: 204 }),
    });

    renderAt("/notifications", <Route path="/notifications" element={<NotificationsPage />} />);

    expect(await screen.findByText("2 unread")).toBeInTheDocument();
    expect(screen.getByText("P1 incident created")).toBeInTheDocument();
    expect(screen.getByText("Claimed by Alice")).toBeInTheDocument();

    const markAllBtn = await screen.findByRole("button", { name: /Mark all as read/i });
    expect(markAllBtn).toBeInTheDocument();

    fireEvent.click(markAllBtn);

    await waitFor(() => {
      const readAllCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/notifications/read-all"));
      expect(readAllCall).toBeDefined();
    });
  });

  test("NotificationsPage displays error message if mark-all-as-read fails", async () => {
    const mockNotifications = [
      { id: 1, user_id: 1, item_id: 10, message: "P1 incident created", read_at: null, created_at: new Date().toISOString() },
    ];

    standardBackend({
      "GET /notifications": () => json(200, mockNotifications),
      "POST /notifications/read-all": () => apiError(500, "internal_error", "Failed to mark all notifications as read"),
    });

    renderAt("/notifications", <Route path="/notifications" element={<NotificationsPage />} />);

    const markAllBtn = await screen.findByRole("button", { name: /Mark all as read/i });
    fireEvent.click(markAllBtn);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to mark all notifications as read");
  });

  test("NotificationsPage hides Mark all as read button when all notifications are already read", async () => {
    const mockNotifications = [
      { id: 1, user_id: 1, item_id: 10, message: "P1 incident created", read_at: "2026-10-18T00:00:00Z", created_at: "2026-10-18T00:00:00Z" },
    ];

    standardBackend({
      "GET /notifications": () => json(200, mockNotifications),
    });

    renderAt("/notifications", <Route path="/notifications" element={<NotificationsPage />} />);

    expect(await screen.findByText("Notifications")).toBeInTheDocument();
    expect(screen.queryByText(/unread/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark all as read/i })).not.toBeInTheDocument();
  });
});
