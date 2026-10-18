import type { ItemType, Status } from "./api/types";

export const statusLabel: Record<Status, string> = {
  OPEN: "Open",
  IN_PROGRESS: "In progress",
  AWAITING_APPROVAL: "Awaiting approval",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

export const typeLabel: Record<ItemType, string> = {
  incident: "Incident",
  customer_issue: "Customer issue",
  payment: "Payment",
  compliance: "Compliance",
  task: "Task",
};

export const actionLabel: Record<string, string> = {
  start: "Start work",
  release: "Release",
  submit: "Submit for approval",
  resolve: "Resolve",
  approve: "Approve",
  reject: "Reject",
  close: "Close",
  reopen: "Reopen",
  cancel: "Cancel item",
};

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** `<input type="datetime-local">` speaks local time without a zone; the API speaks ISO 8601 with one. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const fromLocalInput = (value: string): string | null => (value ? new Date(value).toISOString() : null);
