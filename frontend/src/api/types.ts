// Mirrors backend/app/schemas.py.

export interface HealthResponse {
  status: "healthy" | "degraded";
  version: string;
  database: string;
  service: string;
}

export type Role = "VIEWER" | "MEMBER" | "LEAD";
export type ItemType = "incident" | "customer_issue" | "payment" | "compliance" | "task";
export type Priority = "P1" | "P2" | "P3" | "P4";
export type Status = "OPEN" | "IN_PROGRESS" | "AWAITING_APPROVAL" | "RESOLVED" | "CLOSED" | "CANCELLED";
export type TransitionAction =
  | "start"
  | "release"
  | "submit"
  | "resolve"
  | "approve"
  | "reject"
  | "close"
  | "reopen"
  | "cancel";
export type View = "assigned_to_me" | "unassigned_urgent" | "awaiting_my_approval";

export const ITEM_TYPES: ItemType[] = ["incident", "customer_issue", "payment", "compliance", "task"];
export const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];
export const STATUSES: Status[] = ["OPEN", "IN_PROGRESS", "AWAITING_APPROVAL", "RESOLVED", "CLOSED", "CANCELLED"];
export const TRANSITION_ACTIONS: TransitionAction[] = [
  "start",
  "submit",
  "resolve",
  "approve",
  "reject",
  "release",
  "reopen",
  "close",
  "cancel",
];
// The server rejects these without a comment, so the UI asks for one up front.
export const COMMENT_REQUIRED: TransitionAction[] = ["reject", "reopen"];

export interface Ref {
  id: number;
  name: string;
}

export interface Me {
  id: number;
  name: string;
  email: string;
  is_admin: boolean;
  memberships: { team: Ref; role: Role }[];
}

export interface Member {
  user: Ref;
  role: Role;
}

export interface ItemSummary {
  id: number;
  team: Ref;
  type: ItemType;
  title: string;
  priority: Priority;
  status: Status;
  assignee: Ref | null;
  requires_approval: boolean;
  due_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Item extends ItemSummary {
  description: string;
  created_by: Ref;
  resolved_at: string | null;
  allowed_actions: string[];
}

export interface ItemPage {
  items: ItemSummary[];
  next_cursor: string | null;
}

export interface DashboardCounts {
  assigned_to_me: number;
  unassigned_urgent: number;
  awaiting_my_approval: number;
}

export interface ActivityEvent {
  id: number;
  kind: "created" | "status_changed" | "assigned" | "priority_changed" | "edited" | "commented";
  actor: Ref;
  from_value: unknown;
  to_value: unknown;
  comment: string | null;
  created_at: string;
}

export interface ActivityPage {
  events: ActivityEvent[];
  next_cursor: string | null;
}

export interface Notification {
  id: number;
  item_id: number;
  message: string;
  read_at: string | null;
  created_at: string;
}

export interface ItemFilters {
  view?: View;
  team_id?: string;
  status?: Status[];
  priority?: Priority[];
  type?: ItemType[];
  assignee?: string;
  q?: string;
  sort?: "-updated_at" | "priority";
}

export interface ItemCreate {
  team_id: number;
  type: ItemType;
  title: string;
  description: string;
  priority: Priority;
  due_at: string | null;
  requires_approval: boolean;
}

export interface ItemUpdate {
  title?: string;
  description?: string;
  priority?: Priority;
  due_at?: string | null;
}
