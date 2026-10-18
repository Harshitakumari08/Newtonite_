import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useUpdateItem } from "../api/hooks";
import { PRIORITIES, type Item, type ItemUpdate, type Priority } from "../api/types";
import { fromLocalInput, toLocalInput } from "../format";
import ErrorMessage from "./ErrorMessage";

interface Draft {
  title: string;
  description: string;
  priority: Priority;
  due: string;
}

const draftOf = (item: Item): Draft => ({
  title: item.title,
  description: item.description,
  priority: item.priority,
  due: toLocalInput(item.due_at),
});

const fieldLabel = (field: string) => (field === "due_at" ? "due date" : field);

function changes(base: Draft, draft: Draft): ItemUpdate {
  const out: ItemUpdate = {};
  if (draft.title !== base.title) out.title = draft.title;
  if (draft.description !== base.description) out.description = draft.description;
  if (draft.priority !== base.priority) out.priority = draft.priority;
  if (draft.due !== base.due) out.due_at = fromLocalInput(draft.due);
  return out;
}

/**
 * The edit is based on the version the user started from, not whatever the latest poll brought in.
 * If someone else saved in between, the server answers 409 version_conflict with the current item. We keep
 * the user's draft, show what changed, and let them re-apply it on top of the latest version or drop it.
 */
export default function EditForm({ item, onDone }: { item: Item; onDone: () => void }) {
  const canEdit = item.allowed_actions.includes("edit");
  const canPriority = item.allowed_actions.includes("change_priority");
  const [base, setBase] = useState(() => ({ version: item.version, draft: draftOf(item) }));
  const [draft, setDraft] = useState(base.draft);
  const update = useUpdateItem(item.id);

  const conflict = update.error instanceof ApiError && update.error.code === "version_conflict";
  const latest = conflict ? (update.error as ApiError).currentItem : undefined;
  const pending = changes(base.draft, draft);
  const theirChanges = latest ? Object.keys(changes(base.draft, draftOf(latest))) : [];
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  function save(e?: FormEvent) {
    e?.preventDefault();
    if (!Object.keys(pending).length) return onDone();
    update.run({ id: item.id, version: base.version, data: pending }, { onSuccess: onDone });
  }

  function reapply() {
    if (!latest) return;
    // Rebase: their changes become the new baseline, and only the fields this user touched stay as drafted.
    const theirs = draftOf(latest);
    const mine = changes(base.draft, draft);
    const merged: Draft = { ...theirs };
    if (mine.title !== undefined) merged.title = draft.title;
    if (mine.description !== undefined) merged.description = draft.description;
    if (mine.priority !== undefined) merged.priority = draft.priority;
    if ("due_at" in mine) merged.due = draft.due;
    setBase({ version: latest.version, draft: theirs });
    setDraft(merged);
    update.reset();
  }

  return (
    <form className="card form" onSubmit={save}>
      {conflict && latest && (
        <div className="alert alert-warn" role="alert">
          <strong>Someone else changed this item while you were editing.</strong> Your changes were not saved. The
          latest version is shown on the page.
          <ul className="small">
            {theirChanges.map((field) => (
              <li key={field}>
                They changed the {fieldLabel(field)}
                {field in pending && <strong>, which you changed too; re-applying keeps yours</strong>}.
              </li>
            ))}
          </ul>
          <div className="actions">
            <button type="button" className="primary" onClick={reapply}>
              Re-apply my changes to the latest version
            </button>
            <button type="button" onClick={onDone}>
              Discard my changes
            </button>
          </div>
        </div>
      )}
      <label>
        Title
        <input
          value={draft.title}
          onChange={(e) => set("title", e.target.value)}
          disabled={!canEdit}
          minLength={3}
          maxLength={200}
          required
        />
      </label>
      <label>
        Description
        <textarea
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
          disabled={!canEdit}
          rows={6}
        />
      </label>
      <div className="row">
        <label>
          Priority
          <select
            value={draft.priority}
            onChange={(e) => set("priority", e.target.value as Priority)}
            disabled={!canPriority}
          >
            {PRIORITIES.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          Due
          <input
            type="datetime-local"
            value={draft.due}
            onChange={(e) => set("due", e.target.value)}
            disabled={!canEdit}
          />
        </label>
      </div>
      {!conflict && <ErrorMessage error={update.error} onRetry={update.retry} />}
      <div className="actions">
        <button type="submit" className="primary" disabled={update.isPending || conflict}>
          {update.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onDone} disabled={update.isPending}>
          Cancel
        </button>
      </div>
    </form>
  );
}
