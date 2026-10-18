import { Link } from "react-router-dom";

/** Shown for items outside the user's teams too: the server answers 404, not 403, so ids don't leak. */
export default function NotFound({ what = "Item" }: { what?: string }) {
  return (
    <div className="empty-state">
      <h1>{what} not found</h1>
      <p>It doesn't exist, or it belongs to a team you're not part of.</p>
      <Link to="/">Back to dashboard</Link>
    </div>
  );
}
