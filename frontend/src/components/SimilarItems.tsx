import { Link } from "react-router-dom";
import { useSimilar } from "../api/hooks";
import { StatusBadge } from "./Badges";

/** Other items with overlapping words, so people spot duplicates and earlier fixes. Hidden when there are none. */
export default function SimilarItems({ itemId }: { itemId: number }) {
  const similar = useSimilar(itemId);
  if (!similar.data?.length) return null;
  return (
    <section className="panel similar">
      <div className="panel-head">
        <h2>Similar items</h2>
      </div>
      <ul>
        {similar.data.map((s) => (
          <li key={s.id}>
            <Link to={`/items/${s.id}`}>
              <span className="muted">#{s.id}</span> {s.title}
            </Link>
            <StatusBadge status={s.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}
