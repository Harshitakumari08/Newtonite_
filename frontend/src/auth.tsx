import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setUnauthorizedHandler, tokenStore } from "./api/client";
import { useMe } from "./api/hooks";
import type { Me, Role } from "./api/types";

interface Auth {
  me: Me | undefined;
  loading: boolean;
  signedIn: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  roleIn: (teamId: number) => Role | undefined;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [token, setToken] = useState(tokenStore.get());
  const meQuery = useMe(!!token);

  const logout = () => {
    tokenStore.clear();
    setToken(null);
    qc.clear();
  };

  // An expired or revoked token anywhere in the app sends the user back to the login screen.
  useEffect(() => setUnauthorizedHandler(logout));

  const value: Auth = {
    me: meQuery.data,
    loading: !!token && meQuery.isPending,
    signedIn: !!token,
    async login(email, password) {
      const { access_token } = await api.login(email, password);
      tokenStore.set(access_token);
      qc.clear();
      setToken(access_token);
    },
    logout,
    roleIn(teamId) {
      const me = meQuery.data;
      if (!me) return undefined;
      if (me.is_admin) return "LEAD";
      return me.memberships.find((m) => m.team.id === teamId)?.role;
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}
