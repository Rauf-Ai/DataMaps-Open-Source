import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, post } from "./api.js";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [me, setMe] = useState(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const m = await api("/api/auth/me");
      setMe(m);
    } catch {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      await reload();
      setLoading(false);
    })();
  }, [reload]);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout", {});
    } catch {}
    setMe(null);
    window.location.href = "/";
  }, []);

  return <AuthCtx.Provider value={{ me, loading, reload, logout }}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  return useContext(AuthCtx);
}
