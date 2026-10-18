import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useNotifications } from "../api/hooks";
import { useAuth } from "../auth";
import { useTheme } from "../theme";
import {
  IconAlertTriangle,
  IconBell,
  IconBrand,
  IconCheckCircle,
  IconDashboard,
  IconItems,
  IconMenu,
  IconMoon,
  IconPlus,
  IconSignOut,
  IconSun,
  IconUser,
  IconX,
} from "./Icons";

export default function Layout() {
  const { me, logout } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const notifications = useNotifications();
  const unread = notifications.data?.filter((n) => !n.read_at).length ?? 0;
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  // Close mobile drawer on route change
  const handleNavClick = () => {
    setMobileOpen(false);
  };

  return (
    <div className="app-shell">
      {/* Mobile Drawer Backdrop */}
      <div
        className={`sidebar-backdrop ${mobileOpen ? "open" : ""}`}
        onClick={() => setMobileOpen(false)}
        aria-hidden="true"
      />

      {/* Persistent Desktop Sidebar / Mobile Off-canvas Drawer */}
      <aside className={`app-sidebar ${mobileOpen ? "open" : ""}`} aria-label="Main sidebar">
        <div className="sidebar-header">
          <Link to="/" className="brand-wrapper" onClick={handleNavClick}>
            <div className="brand-icon" aria-hidden="true">
              <IconBrand size={18} />
            </div>
            <div className="brand-info">
              <span className="brand-title">Ops Queue</span>
              <span className="brand-tagline">Newtonite Ops</span>
            </div>
          </Link>
          {mobileOpen && (
            <button
              type="button"
              className="theme-toggle-btn"
              onClick={() => setMobileOpen(false)}
              aria-label="Close navigation menu"
            >
              <IconX size={18} />
            </button>
          )}
        </div>

        <div className="sidebar-content">
          <nav className="sidebar-group" aria-label="Primary navigation">
            <NavLink to="/" end className="sidebar-link" onClick={handleNavClick}>
              <IconDashboard size={16} />
              <span className="link-label">Dashboard</span>
            </NavLink>
            <NavLink to="/items" end className="sidebar-link" onClick={handleNavClick}>
              <IconItems size={16} />
              <span className="link-label">Items</span>
            </NavLink>
            <NavLink to="/items/new" className="sidebar-link" onClick={handleNavClick}>
              <IconPlus size={16} />
              <span className="link-label">New item</span>
            </NavLink>
            <NavLink to="/notifications" className="sidebar-link" onClick={handleNavClick}>
              <IconBell size={16} />
              <span className="link-label">Notifications</span>
              {unread > 0 && <span className="count">{unread}</span>}
            </NavLink>
          </nav>

          <div className="sidebar-group">
            <div className="sidebar-section-title">Quick Queues</div>
            <NavLink
              to="/items?view=assigned_to_me"
              className={`sidebar-link ${location.search.includes("view=assigned_to_me") ? "active" : ""}`}
              onClick={handleNavClick}
            >
              <IconUser size={15} />
              <span className="link-label">Assigned to me</span>
            </NavLink>
            <NavLink
              to="/items?view=unassigned_urgent"
              className={`sidebar-link ${location.search.includes("view=unassigned_urgent") ? "active" : ""}`}
              onClick={handleNavClick}
            >
              <IconAlertTriangle size={15} />
              <span className="link-label">Unassigned urgent</span>
            </NavLink>
            <NavLink
              to="/items?view=awaiting_my_approval"
              className={`sidebar-link ${location.search.includes("view=awaiting_my_approval") ? "active" : ""}`}
              onClick={handleNavClick}
            >
              <IconCheckCircle size={15} />
              <span className="link-label">Awaiting approval</span>
            </NavLink>
          </div>
        </div>

        <div className="sidebar-footer">
          {me && (
            <div
              className="user-card"
              title={me.memberships.map((m) => `${m.team.name}: ${m.role}`).join("\n")}
            >
              <div className="user-avatar" aria-hidden="true">
                {me.name.charAt(0).toUpperCase()}
              </div>
              <div className="user-details">
                <span className="user-name">{me.name}</span>
                <span className="user-role-label">
                  {me.is_admin ? "Admin" : me.memberships[0]?.role?.toLowerCase() ?? "Member"}
                </span>
              </div>
              {me.is_admin && <span className="badge admin-badge">admin</span>}
            </div>
          )}

          <div className="sidebar-footer-actions">
            <button
              type="button"
              className="sign-out-btn"
              onClick={logout}
              title="Sign out"
              aria-label="Sign out"
            >
              <IconSignOut size={15} />
              <span>Sign out</span>
            </button>
          </div>
        </div>
      </aside>

      {/* Main Workspace Layout */}
      <div className="app-main">
        <header className="app-topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="mobile-nav-toggle"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation menu"
            >
              <IconMenu size={20} />
            </button>
            <div className="topbar-context-badge">
              <span>Ops Queue</span>
              <span>/</span>
              <span>Workspace</span>
            </div>
          </div>

          <div className="topbar-right">
            <button
              type="button"
              className="theme-toggle-btn"
              onClick={toggleTheme}
              aria-label={resolvedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={resolvedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {resolvedTheme === "dark" ? <IconSun size={16} /> : <IconMoon size={16} />}
            </button>
          </div>
        </header>

        <main>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
