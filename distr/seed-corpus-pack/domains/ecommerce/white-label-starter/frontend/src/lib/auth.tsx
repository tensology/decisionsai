import React, { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api";

type Profile = {
  id: number;
  email: string;
  username: string;
  name?: string;
  phone?: string;
  email_verified?: boolean;
};

type AuthCtx = {
  user: Profile | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>({ user: null, loading: true, refresh: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const p = await api.get<Profile>("/api/auth/profile/");
      setUser(p);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const logout = async () => {
    await api.post("/api/auth/logout/");
    setUser(null);
  };

  return <Ctx.Provider value={{ user, loading, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
