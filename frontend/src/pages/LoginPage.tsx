import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { errorText } from "../components/ErrorMessage";
import {
  IconBrand,
  IconClock,
  IconHistory,
  IconItems,
  IconShield,
} from "../components/Icons";
import heroIllustration from "../assets/ops-queue-flow.jpg";

interface DemoUserMeta {
  id: string;
  name: string;
  role: string;
  teams: string;
  desc: string;
}

const DEMO_USERS_META: Record<string, DemoUserMeta> = {
  alice: {
    id: "alice",
    name: "Alice Lead",
    role: "Lead",
    teams: "Support",
    desc: "Can approve Support work items, triage tickets, and assign work.",
  },
  bob: {
    id: "bob",
    name: "Bob Member",
    role: "Member",
    teams: "Support & Payments",
    desc: "Can claim open items, submit payment adjustments, and resolve tickets.",
  },
  carol: {
    id: "carol",
    name: "Carol Member",
    role: "Member",
    teams: "Support",
    desc: "Can create and work on Support tickets; competes in claim races.",
  },
  priya: {
    id: "priya",
    name: "Priya Lead",
    role: "Lead",
    teams: "Payments",
    desc: "Enforces four-eyes dual control: approves financial adjustments submitted by others.",
  },
  dana: {
    id: "dana",
    name: "Dana Member",
    role: "Member",
    teams: "Engineering & Support",
    desc: "Cross-functional engineer handling production incidents and triage.",
  },
  victor: {
    id: "victor",
    name: "Victor Viewer",
    role: "Viewer",
    teams: "Support & Compliance",
    desc: "Read-only queue access and comments. Prohibited from claiming or editing.",
  },
  admin: {
    id: "admin",
    name: "Admin",
    role: "Global Administrator",
    teams: "All Teams",
    desc: "Full system visibility with Lead capabilities across all operational queues.",
  },
};

const DEMO_USERS = ["alice", "bob", "carol", "priya", "dana", "victor", "admin"];

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [selectedUser, setSelectedUser] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selectedMeta = selectedUser ? DEMO_USERS_META[selectedUser] : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      const from = (location.state as { from?: { pathname: string; search: string } } | null)?.from;
      navigate(from ? from.pathname + from.search : "/", { replace: true });
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-shell">
        <div className="login-hero">
          <div className="login-badge">
            <IconBrand size={16} />
            <span>Ops Queue</span>
          </div>

          <h1>Operational work, under control.</h1>

          <p className="lead">
            Coordinate investigations, track team ownership, and enforce dual-control approvals.
          </p>

          <div className="login-visual-card">
            <img
              src={heroIllustration}
              alt="Ops Queue workflow pipeline"
              className="login-hero-img"
            />
            <div className="login-visual-badges">
              <span className="login-badge-pill">
                <IconShield size={13} />
                <span>Four-eyes approval</span>
              </span>
              <span className="login-badge-pill">
                <IconClock size={13} />
                <span>Safe concurrency</span>
              </span>
              <span className="login-badge-pill">
                <IconItems size={13} />
                <span>Team ownership</span>
              </span>
              <span className="login-badge-pill">
                <IconHistory size={13} />
                <span>Audit history</span>
              </span>
            </div>
          </div>
        </div>

        <form onSubmit={submit} className="card">
          <div className="login-header">
            <h2>Sign in</h2>
            <p>Select a demo persona below or enter your credentials</p>
          </div>

          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setSelectedUser(null);
              }}
              placeholder="name@example.com"
              autoFocus
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </label>
          {error && (
            <div className="alert alert-error" role="alert">
              {error}
            </div>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <div className="demo-users-box">
            <div className="demo-users-title">Demo accounts (password: <code>password</code>)</div>
            <div className="demo-pills">
              {DEMO_USERS.map((u) => (
                <button
                  key={u}
                  type="button"
                  className={`demo-pill ${selectedUser === u ? "active" : ""}`}
                  onClick={() => {
                    setEmail(`${u}@example.com`);
                    setPassword("password");
                    setSelectedUser(u);
                  }}
                >
                  {u}
                </button>
              ))}
            </div>
            {selectedMeta && (
              <div className="demo-persona-desc">
                <span className="demo-persona-name">{selectedMeta.name}</span> · {selectedMeta.teams} ({selectedMeta.role})
                <div className="demo-persona-detail">{selectedMeta.desc}</div>
              </div>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
