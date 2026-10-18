import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateItem } from "../api/hooks";
import { ITEM_TYPES, PRIORITIES, type ItemType, type Priority } from "../api/types";
import { useAuth } from "../auth";
import { fromLocalInput, typeLabel } from "../format";
import ErrorMessage from "../components/ErrorMessage";

const ALWAYS_APPROVAL: ItemType[] = ["payment", "compliance"];

export default function NewItemPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const create = useCreateItem();
  const teams = me?.is_admin
    ? me.memberships.map((m) => m.team)
    : (me?.memberships.filter((m) => m.role !== "VIEWER").map((m) => m.team) ?? []);

  const [teamId, setTeamId] = useState<number | "">(teams[0]?.id ?? "");
  const [type, setType] = useState<ItemType>("task");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<Priority>("P3");
  const [due, setDue] = useState("");
  const [requiresApproval, setRequiresApproval] = useState(false);

  useEffect(() => {
    if (teamId === "" && teams.length > 0) {
      setTeamId(teams[0].id);
    }
  }, [teams, teamId]);

  if (me && !teams.length) {
    const isViewerOnly = me.memberships.length > 0 && me.memberships.every((m) => m.role === "VIEWER");
    return (
      <div className="empty-state">
        <p className="muted">
          {isViewerOnly
            ? "You're a viewer in all your teams, so you can't create items."
            : "You don't belong to any teams where you have permission to create items."}
        </p>
      </div>
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (teamId === "" || create.isPending) return;
    create.run(
      {
        data: {
          team_id: teamId,
          type,
          title,
          description,
          priority,
          due_at: fromLocalInput(due),
          requires_approval: requiresApproval || ALWAYS_APPROVAL.includes(type),
        },
      },
      { onSuccess: (item) => navigate(`/items/${item.id}`, { replace: true }) },
    );
  }

  return (
    <div className="new-item-container">
      <div className="item-breadcrumbs">
        <Link to="/items" className="breadcrumb-link">
          Items
        </Link>
        <span>/</span>
        <span className="muted">New</span>
      </div>
      <form className="card form" onSubmit={submit}>
        <div className="form-header">
          <h1>New item</h1>
          <p className="muted small">Create an operational work item with automatic team routing and workflow controls.</p>
        </div>

        <div className="form-section">
          <div className="row">
            <label>
              Team
              <select value={teamId} onChange={(e) => setTeamId(Number(e.target.value))} required>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Type
              <select value={type} onChange={(e) => setType(e.target.value as ItemType)}>
                {ITEM_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {typeLabel[t]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Priority
              <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
                {PRIORITIES.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <label>
              Due
              <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
            </label>
          </div>
        </div>

        <div className="form-section">
          <label>
            Title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              minLength={3}
              maxLength={200}
              placeholder="Short, actionable summary of the work item"
              required
            />
          </label>
          <label>
            Description
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={6}
              placeholder="Provide background, reproduction steps, or context..."
            />
          </label>
        </div>

        <div className="form-section">
          <label className="inline">
            <input
              type="checkbox"
              checked={requiresApproval || ALWAYS_APPROVAL.includes(type)}
              disabled={ALWAYS_APPROVAL.includes(type)}
              onChange={(e) => setRequiresApproval(e.target.checked)}
            />
            Needs a Lead's approval before it's resolved
            {ALWAYS_APPROVAL.includes(type) && <span className="muted small"> (always, for {typeLabel[type]})</span>}
          </label>
        </div>

        <ErrorMessage error={create.error} onRetry={create.retry} />

        <div className="actions">
          <button type="submit" className="primary" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create item"}
          </button>
          <Link to="/items" className="btn-secondary" style={{ textDecoration: "none" }}>
            <button type="button">Cancel</button>
          </Link>
        </div>
      </form>
    </div>
  );
}
