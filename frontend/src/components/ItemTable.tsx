import { Link } from "react-router-dom";
import type { ItemSummary } from "../api/types";
import { typeLabel, timeAgo } from "../format";
import { PriorityBadge, StatusBadge } from "./Badges";

export default function ItemTable({ items, compact = false }: { items: ItemSummary[]; compact?: boolean }) {
  if (!items.length) return <p className="muted" style={{ padding: "16px 0", textAlign: "center" }}>Nothing here.</p>;
  return (
    <div className="table-container">
      <table className="items">
        <thead>
          <tr>
            <th style={{ width: 64 }}>#</th>
            <th>Title</th>
            <th style={{ width: 90 }}>Priority</th>
            <th style={{ width: 140 }}>Status</th>
            {!compact && <th style={{ width: 120 }}>Type</th>}
            <th style={{ width: 120 }}>Team</th>
            <th style={{ width: 140 }}>Assignee</th>
            <th style={{ width: 110 }}>Updated</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td className="item-id">#{item.id}</td>
              <td className="item-title">
                <Link to={`/items/${item.id}`}>{item.title}</Link>
                {item.requires_approval && <span className="tag">approval</span>}
              </td>
              <td>
                <PriorityBadge priority={item.priority} />
              </td>
              <td>
                <StatusBadge status={item.status} />
              </td>
              {!compact && <td style={{ color: "var(--text-secondary)" }}>{typeLabel[item.type]}</td>}
              <td style={{ fontWeight: 500 }}>{item.team.name}</td>
              <td>
                {item.assignee ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span
                      style={{
                        width: 20,
                        height: 20,
                        borderRadius: "50%",
                        backgroundColor: "var(--surface-subtle)",
                        color: "var(--text-secondary)",
                        border: "1px solid var(--border)",
                        fontSize: 10,
                        fontWeight: 600,
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                      aria-hidden="true"
                    >
                      {item.assignee.name.charAt(0).toUpperCase()}
                    </span>
                    <span>{item.assignee.name}</span>
                  </span>
                ) : (
                  <span className="muted" style={{ fontStyle: "italic", fontSize: "0.8rem" }}>Unassigned</span>
                )}
              </td>
              <td className="muted" title={item.updated_at}>
                {timeAgo(item.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
