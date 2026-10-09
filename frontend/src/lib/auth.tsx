import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api";

interface User {
  id: number;
  name: string;
}

interface AuthState {
  user: User | null;
  checking: boolean;
  login: (name: string, pin: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api.get<{ user: User; csrf_token: string }>("/api/auth/me")
      .then((r) => {
        setCsrfToken(r.csrf_token);
        setUser(r.user);
      })
      .catch((e) => {
        if (!(e instanceof ApiError) || e.status !== 401) console.warn("Session check failed");
      })
      .finally(() => setChecking(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (name: string, pin: string) => {
    const r = await api.post<{ user: User; csrf_token: string }>("/api/auth/login", { name, pin });
    setCsrfToken(r.csrf_token);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setCsrfToken("");
      setUser(null);
    }
  }, []);

  return <AuthContext.Provider value={{ user, checking, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
