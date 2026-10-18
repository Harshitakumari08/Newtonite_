import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAssign, useClaim, useItem, useMembers, useTransition } from "../api/hooks";
import { COMMENT_REQUIRED, TRANSITION_ACTIONS, type Item, type TransitionAction } from "../api/types";
import { useAuth } from "../auth";
import { actionLabel, dateTime, typeLabel } from "../format";
import Activity from "../components/Activity";
import { PriorityBadge, StatusBadge } from "../components/Badges";
import EditForm from "../components/EditForm";
import ErrorMessage from "../components/ErrorMessage";
import NotFound from "../components/NotFound";
import SimilarItems from "../components/SimilarItems";

export default function ItemPage() {
  const id = Number(useParams().id);
  if (!Number.isInteger(id) || id <= 0) return <NotFound />;
  return <ItemView key={id} id={id} />;
}

function ItemView({ id }: { id: number }) {
  const item = useItem(id);
  const [editing, setEditing] = useState(false);

  if (item.isPending) return <p className="muted">Loading…</p>;
  if (item.error instanceof ApiError && item.error.status === 404) return <NotFound />;
  if (item.error && !item.data) return <ErrorMessage error={item.error} onRetry={() => item.refetch()} />;
  const it = item.data!;
  const canEdit = it.allowed_actions.includes("edit") || it.allowed_actions.includes("change_priority");

  return (
    <div className="item-page">
      <div className="item-head">
        <div className="item-breadcrumbs">
          <Link to="/items" className="breadcrumb-link">
            Items
          </Link>
          <span>/</span>
          <span className="muted">#{it.id}</span>
        </div>
        <h1>{it.title}</h1>
        <div className="meta">
          <StatusBadge status={it.status} />
          <PriorityBadge priority={it.priority} />
          <span>{typeLabel[it.type]}</span>
          <span>· {it.team.name}</span>
          {it.requires_approval && (
            <span className="tag">
              needs approval
            </span>
          )}
          <span className="muted small">v{it.version}</span>
        </div>
      </div>

      <div className="item-grid">
        <div>
          {editing ? (
            <EditForm item={it} onDone={() => setEditing(false)} />
          ) : (
            <section className="panel">
              <div className="panel-head">
                <h2>Description</h2>
                {canEdit && <button onClick={() => setEditing(true)}>Edit</button>}
              </div>
              <p className="description">{it.description || <span className="muted">No description.</span>}</p>
            </section>
          )}
          <Activity itemId={it.id} canComment={it.allowed_actions.includes("comment")} />
        </div>
        <aside>
          <Actions item={it} />
          <section className="panel details">
            <div className="panel-head">
              <h2>Details</h2>
            </div>
            <dl>
              <dt>Assignee</dt>
              <dd>{it.assignee?.name ?? <span className="muted">Unassigned</span>}</dd>
              <dt>Created by</dt>
              <dd>{it.created_by.name}</dd>
              <dt>Created</dt>
              <dd>{dateTime(it.created_at)}</dd>
              <dt>Updated</dt>
              <dd>{dateTime(it.updated_at)}</dd>
              {it.due_at && (
                <>
                  <dt>Due</dt>
                  <dd>{dateTime(it.due_at)}</dd>
                </>
              )}
              {it.resolved_at && (
                <>
                  <dt>Resolved</dt>
                  <dd>{dateTime(it.resolved_at)}</dd>
                </>
              )}
            </dl>
          </section>
          <SimilarItems itemId={it.id} />
        </aside>
      </div>
    </div>
  );
}

/** A 409 means the item moved on under us; the cache already holds the server's current copy. */
function ConflictNotice({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (!(error instanceof ApiError)) return <ErrorMessage error={error} onRetry={onRetry} />;
  if (error.status !== 409) return <ErrorMessage error={error} />;
  const current = error.currentItem;
  const text =
    error.code === "already_claimed"
      ? `${current?.assignee?.name ?? "Someone else"} claimed this item first.`
      : error.code === "version_conflict"
        ? "Someone else changed this item a moment ago, so nothing was done. Here's the latest version; check it and try again if it still makes sense."
        : `${error.message}. The page now shows the latest state.`;
  return (
    <div className="alert alert-warn" role="alert">
      {text}
    </div>
  );
}

function Actions({ item }: { item: Item }) {
  const { me } = useAuth();
  const claim = useClaim(item.id, me);
  const transition = useTransition(item.id);
  const assign = useAssign(item.id);
  const [pendingAction, setPendingAction] = useState<TransitionAction | null>(null);
  const [comment, setComment] = useState("");

  const transitions = TRANSITION_ACTIONS.filter((a) => item.allowed_actions.includes(a));
  const canClaim = item.allowed_actions.includes("claim");
  const canAssign = item.allowed_actions.includes("assign");
  const busy = claim.isPending || transition.isPending || assign.isPending;
  const lastError = claim.error ?? transition.error ?? assign.error;

  function resetErrors() {
    claim.reset();
    transition.reset();
    assign.reset();
  }

  function act(action: TransitionAction) {
    resetErrors();
    if (COMMENT_REQUIRED.includes(action)) {
      setPendingAction(action);
      return;
    }
    if (action === "cancel" && !window.confirm("Cancel this item? Cancelled items can't be reopened.")) return;
    transition.run({ id: item.id, version: item.version, action });
  }

  function submitWithComment() {
    if (!pendingAction || !comment.trim()) return;
    transition.run(
      { id: item.id, version: item.version, action: pendingAction, comment },
      {
        onSuccess: () => {
          setPendingAction(null);
          setComment("");
        },
      },
    );
  }

  const nothingToDo = !canClaim && !canAssign && !transitions.length && !claim.isPending;
  return (
    <section className="panel actions-panel">
      <h2>Actions</h2>
      {lastError && (
        <ConflictNotice
          error={lastError}
          onRetry={claim.error ? claim.retry : transition.error ? transition.retry : assign.retry}
        />
      )}
      {claim.isPending && <p className="muted">Claiming…</p>}
      {nothingToDo && <p className="muted small">There's nothing you can do on this item right now.</p>}

      <div className="actions vertical">
        {canClaim && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              resetErrors();
              claim.run({ id: item.id });
            }}
          >
            Claim
          </button>
        )}
        {transitions.map((a) => (
          <button
            key={a}
            className={a === "approve" || a === "resolve" ? "primary" : a === "cancel" || a === "reject" ? "danger" : ""}
            disabled={busy}
            onClick={() => act(a)}
          >
            {transition.isPending && transition.variables?.action === a ? "Working…" : actionLabel[a]}
          </button>
        ))}
      </div>

      {pendingAction && (
        <div className="comment-required">
          <label>
            Why? (required to {actionLabel[pendingAction].toLowerCase()})
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} autoFocus />
          </label>
          <div className="actions">
            <button className="primary" disabled={!comment.trim() || busy} onClick={submitWithComment}>
              {actionLabel[pendingAction]}
            </button>
            <button onClick={() => setPendingAction(null)}>Never mind</button>
          </div>
        </div>
      )}

      {canAssign && <AssignPicker item={item} disabled={busy} onAssign={(assigneeId) => {
        resetErrors();
        assign.run({ id: item.id, version: item.version, assigneeId });
      }} />}
    </section>
  );
}

function AssignPicker({ item, disabled, onAssign }: { item: Item; disabled: boolean; onAssign: (id: number) => void }) {
  const members = useMembers(item.team.id);
  // Viewers can't own work, so they aren't offered.
  const candidates = (members.data ?? []).filter((m) => m.role !== "VIEWER");
  return (
    <label className="assign">
      Assign to
      <select
        value={item.assignee?.id ?? ""}
        disabled={disabled || members.isPending}
        onChange={(e) => e.target.value && onAssign(Number(e.target.value))}
      >
        <option value="">{item.assignee ? "" : "Choose someone"}</option>
        {candidates.map((m) => (
          <option key={m.user.id} value={m.user.id}>
            {m.user.name} ({m.role.toLowerCase()})
          </option>
        ))}
      </select>
    </label>
  );
}
