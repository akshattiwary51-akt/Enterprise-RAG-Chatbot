import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { authApi } from "../services/endpoints";
import { ensureAccessToken, setAuthLostHandler, tokenStore } from "../services/api";
import type { User } from "../types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setAuthLostHandler(() => setUser(null));
    // Restore the session after a reload: the interceptor refreshes using the stored refresh token.
    (async () => {
      if (tokenStore.getRefresh()) {
        try {
          if (!(await ensureAccessToken())) throw new Error("session expired");
          setUser(await authApi.me());
        } catch { tokenStore.clear(); }
      }
      setLoading(false);
    })();
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password);
    tokenStore.setTokens(res.access_token, res.refresh_token);
    setUser(await authApi.me());
  }, []);

  const logout = useCallback(async () => {
    try { await authApi.logout(tokenStore.getRefresh()); } catch { /* token may already be invalid */ }
    tokenStore.clear();
    setUser(null);
  }, []);

  // Convenience for hiding controls only. The backend enforces every permission regardless.
  const can = useCallback((p: string) => !!user?.permissions?.includes(p), [user]);

  const value = useMemo(() => ({ user, loading, login, logout, can }), [user, loading, login, logout, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}
