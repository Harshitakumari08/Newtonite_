import { Link } from "react-router-dom";
import { useDashboard, useItems } from "../api/hooks";
import { useAuth } from "../auth";
import type { View } from "../api/types";
import ErrorMessage from "../components/ErrorMessage";
import { IconAlertTriangle, IconArrowRight, IconCheckCircle, IconPlus, IconSearch, IconUser } from "../components/Icons";
import ItemTable from "../components/ItemTable";

const PANELS: { view: View; title: string; hint: string; icon: typeof IconUser; color: string }[] = [
  { view: "assigned_to_me", title: "Assigned to me", hint: "Open work you own", icon: IconUser, color: "var(--color-primary)" },
  { view: "unassigned_urgent", title: "Unassigned urgent", hint: "P1 and P2 items nobody has claimed", icon: IconAlertTriangle, color: "var(--color-danger)" },
  { view: "awaiting_my_approval", title: "Awaiting my approval", hint: "Submitted work you can approve as a Lead", icon: IconCheckCircle, color: "var(--color-purple)" },
];

const EMPTY_STATES: Record<View, { title: string; hint: string }> = {
  assigned_to_me: {
    title: "No active assignments",
    hint: "You currently have no open items assigned to you. Claim an unassigned item or create a new request.",
  },
  unassigned_urgent: {
    title: "All urgent items claimed",
    hint: "No unassigned P1 or P2 items requiring immediate operational triage.",
  },
  awaiting_my_approval: {
    title: "No items awaiting your approval",
    hint: "There are no pending submissions requiring four-eyes dual control review for your teams.",
  },
};

export default function DashboardPage() {
  const counts = useDashboard();
  const { me } = useAuth();
  const canCreate = me?.is_admin || me?.memberships.some((m) => m.role !== "VIEWER");

  return (
    <div>
      <div className="dashboard-welcome">
        <div className="dashboard-welcome-main">
          <h1>Dashboard</h1>
          <p className="muted" style={{ margin: 0, fontSize: "0.875rem" }}>
            Operational workload overview and active triage queues.
            {me?.name && <span> Signed in as <strong>{me.name}</strong>.</span>}
          </p>
          {me && (
            <div className="dashboard-membership-tags">
              {me.is_admin && <span className="tag badge-admin">Global Admin</span>}
              {me.memberships.map((m) => (
                <span key={m.team.id} className="tag badge-team">
                  {m.team.name} · {m.role}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="dashboard-actions">
          <Link to="/items" style={{ textDecoration: "none" }}>
            <button type="button" className="ghost" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <IconSearch size={15} />
              <span>Browse all items</span>
            </button>
          </Link>
          {canCreate && (
            <Link to="/items/new" style={{ textDecoration: "none" }}>
              <button type="button" className="primary" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <IconPlus size={15} />
                <span>New item</span>
              </button>
            </Link>
          )}
        </div>
      </div>

      <div className="dashboard-guide">
        <div className="dashboard-guide-item">
          <span className="guide-dot dot-urgent" aria-hidden="true" />
          <span><strong>Unassigned urgent:</strong> High-priority P1/P2 items needing claim and triage</span>
        </div>
        <div className="dashboard-guide-item">
          <span className="guide-dot dot-approval" aria-hidden="true" />
          <span><strong>Awaiting approval:</strong> Four-eyes dual control reviews awaiting Lead decision</span>
        </div>
        <div className="dashboard-guide-item">
          <span className="guide-dot dot-assigned" aria-hidden="true" />
          <span><strong>Assigned to me:</strong> Open tasks and investigations actively owned by you</span>
        </div>
      </div>

      <ErrorMessage error={counts.error} onRetry={() => counts.refetch()} />

      <div className="tiles">
        {PANELS.map((p) => {
          const Icon = p.icon;
          return (
            <Link key={p.view} to={`/items?view=${p.view}`} className="tile" aria-label={`${p.title}: ${counts.data ? counts.data[p.view] : "Loading"}`}>
              <div className="tile-top">
                <span className="tile-count">{counts.data ? counts.data[p.view] : "–"}</span>
                <span
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 34,
                    height: 34,
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--surface-subtle)",
                    border: "1px solid var(--border)",
                    color: p.color,
                  }}
                  aria-hidden="true"
                >
                  <Icon size={18} />
                </span>
              </div>
              <span className="tile-title">{p.title}</span>
              <span className="muted small tile-hint">{p.hint}</span>
            </Link>
          );
        })}
      </div>

      {PANELS.map((p) => (
        <Panel key={p.view} view={p.view} title={p.title} />
      ))}
    </div>
  );
}

function Panel({ view, title }: { view: View; title: string }) {
  // Urgent work first; the rest by most recently touched.
  const items = useItems({ view, sort: view === "unassigned_urgent" ? "priority" : "-updated_at" }, 5);
  const rows = items.data?.pages[0]?.items ?? [];

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <Link to={`/items?view=${view}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "0.85rem" }}>
          <span>See all</span>
          <IconArrowRight size={14} />
        </Link>
      </div>
      {items.isPending ? (
        <p className="muted" style={{ padding: "16px 0", textAlign: "center" }}>Loading…</p>
      ) : items.error ? (
        <ErrorMessage error={items.error} onRetry={() => items.refetch()} />
      ) : rows.length === 0 ? (
        <div className="panel-empty-desc">
          <h4>{EMPTY_STATES[view].title}</h4>
          <p>{EMPTY_STATES[view].hint}</p>
        </div>
      ) : (
        <ItemTable items={rows} compact />
      )}
    </section>
  );
}
