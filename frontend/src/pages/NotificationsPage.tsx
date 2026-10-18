import { Link } from "react-router-dom";
import { useMarkAllRead, useMarkRead, useNotifications } from "../api/hooks";
import { timeAgo } from "../format";
import ErrorMessage from "../components/ErrorMessage";
import { IconBell, IconCheck } from "../components/Icons";

export default function NotificationsPage() {
  const notifications = useNotifications();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();
  const list = notifications.data ?? [];
  const unreadCount = list.filter((n) => !n.read_at).length;

  return (
    <div>
      <div className="panel-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h1>Notifications</h1>
          {unreadCount > 0 && (
            <span className="badge status-open">{unreadCount} unread</span>
          )}
        </div>
        {unreadCount > 0 && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => markAllRead.mutate()}
            disabled={markAllRead.isPending}
            style={{ fontSize: "0.8125rem", gap: 6 }}
          >
            <IconCheck size={14} />
            <span>{markAllRead.isPending ? "Marking…" : "Mark all as read"}</span>
          </button>
        )}
      </div>
      <ErrorMessage
        error={notifications.error || markAllRead.error || markRead.error}
        onRetry={() => {
          if (notifications.error) notifications.refetch();
          if (markAllRead.error) markAllRead.reset();
          if (markRead.error) markRead.reset();
        }}
      />
      {!notifications.isPending && !list.length && (
        <div className="empty-state">
          <div className="empty-state-icon">
            <IconBell size={36} />
          </div>
          <h3>All caught up</h3>
          <p className="muted">No notifications yet.</p>
        </div>
      )}
      <ul className="notifications">
        {list.map((n) => (
          <li key={n.id} className={n.read_at ? "read" : "unread"}>
            <Link to={`/items/${n.item_id}`} onClick={() => !n.read_at && markRead.mutate(n.id)}>
              {n.message}
            </Link>
            <span className="muted small">{timeAgo(n.created_at)}</span>
            {!n.read_at && (
              <button className="link small" onClick={() => markRead.mutate(n.id)}>
                Mark read
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
