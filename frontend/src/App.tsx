import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import ItemListPage from "./pages/ItemListPage";
import ItemPage from "./pages/ItemPage";
import NewItemPage from "./pages/NewItemPage";
import NotificationsPage from "./pages/NotificationsPage";
import NotFound from "./components/NotFound";

export default function App() {
  const { signedIn, loading } = useAuth();
  const location = useLocation();

  if (!signedIn) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location }} />} />
      </Routes>
    );
  }
  if (loading) return <p className="muted center">Loading…</p>;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="items" element={<ItemListPage />} />
        <Route path="items/new" element={<NewItemPage />} />
        <Route path="items/:id" element={<ItemPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound what="Page" />} />
      </Route>
    </Routes>
  );
}
