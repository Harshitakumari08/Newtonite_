import { useState, type FormEvent } from "react";
import { useActivity, useAddComment } from "../api/hooks";
import type { ActivityEvent, Ref, Status } from "../api/types";
import { dateTime, statusLabel, timeAgo } from "../format";
import ErrorMessage from "./ErrorMessage";
import {
  IconCheckCircle,
  IconClock,
  IconHistory,
  IconMessage,
  IconTag,
  IconUser,
} from "./Icons";

const name = (v: unknown) => (v && typeof v === "object" && "name" in v ? (v as Ref).name : "nobody");
const status = (v: unknown) => statusLabel[v as Status] ?? String(v);

function describe(e: ActivityEvent): string {
  switch (e.kind) {
    case "created":
      return "created this item";
    case "status_changed":
      return `moved it from ${status(e.from_value)} to ${status(e.to_value)}`;
    case "assigned":
      if (!e.to_value) return `unassigned ${name(e.from_value)}`;
      if (e.actor.id === (e.to_value as Ref).id && !e.from_value) return "claimed it";
      return `assigned it to ${name(e.to_value)}`;
    case "priority_changed":
      return `changed priority from ${e.from_value} to ${e.to_value}`;
    case "edited":
      return `edited ${Object.keys((e.to_value as object) ?? {}).join(", ").replace("due_at", "due date")}`;
    case "commented":
      return "commented";
  }
}

function eventIcon(kind: ActivityEvent["kind"]) {
  switch (kind) {
    case "created":
      return <IconHistory size={13} />;
    case "status_changed":
      return <IconCheckCircle size={13} />;
    case "assigned":
      return <IconUser size={13} />;
    case "priority_changed":
      return <IconTag size={13} />;
    case "commented":
      return <IconMessage size={13} />;
    default:
      return <IconClock size={13} />;
  }
}

export default function Activity({ itemId, canComment }: { itemId: number; canComment: boolean }) {
  const activity = useActivity(itemId);
  const events = activity.data?.pages.flatMap((p) => p.events) ?? [];
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>History</h2>
      </div>
      {canComment && <CommentBox itemId={itemId} />}
      <ErrorMessage error={activity.error} onRetry={() => activity.refetch()} />
      <ol className="timeline">
        {events.map((e) => (
          <li key={e.id} className={`event event-${e.kind}`}>
            <div className="event-icon-circle" aria-hidden="true">
              {eventIcon(e.kind)}
            </div>
            <div className="event-content">
              <div>
                <strong>{e.actor.name}</strong> {describe(e)}{" "}
                <span className="muted small" title={dateTime(e.created_at)}>
                  {timeAgo(e.created_at)}
                </span>
              </div>
              {e.comment && <blockquote>{e.comment}</blockquote>}
            </div>
          </li>
        ))}
      </ol>
      {activity.hasNextPage && (
        <button className="load-more" onClick={() => activity.fetchNextPage()} disabled={activity.isFetchingNextPage}>
          {activity.isFetchingNextPage ? "Loading…" : "Show older"}
        </button>
      )}
    </section>
  );
}

function CommentBox({ itemId }: { itemId: number }) {
  const [body, setBody] = useState("");
  const add = useAddComment(itemId);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim() || add.isPending) return;
    add.run({ id: itemId, body }, { onSuccess: () => setBody("") });
  }
  return (
    <form className="comment-box" onSubmit={submit}>
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Add a comment"
        rows={3}
        aria-label="Comment"
      />
      <ErrorMessage error={add.error} onRetry={add.retry} />
      <button type="submit" className="primary" disabled={!body.trim() || add.isPending}>
        {add.isPending ? "Posting…" : "Comment"}
      </button>
    </form>
  );
}
