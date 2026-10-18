import type { Priority, Status } from "../api/types";
import { statusLabel } from "../format";

const PRIO_SYMBOLS: Record<Priority, string> = {
  P1: "▲",
  P2: "■",
  P3: "●",
  P4: "○",
};

export const StatusBadge = ({ status }: { status: Status }) => {
  return (
    <span className={`badge status-${status.toLowerCase()}`}>
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          backgroundColor: "currentColor",
          opacity: 0.85,
          display: "inline-block",
        }}
        aria-hidden="true"
      />
      {statusLabel[status]}
    </span>
  );
};

export const PriorityBadge = ({ priority }: { priority: Priority }) => (
  <span className={`badge prio-${priority.toLowerCase()}`} title={`Priority ${priority}`}>
    <span style={{ fontSize: "9px", lineHeight: 1 }} aria-hidden="true">
      {PRIO_SYMBOLS[priority] ?? "●"}
    </span>
    {priority}
  </span>
);
