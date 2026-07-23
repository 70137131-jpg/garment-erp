import { createContext, ReactNode, useContext } from "react";
import { AuthUser } from "../api/client";

const AuthorizationContext = createContext<AuthUser | null>(null);

export function AuthorizationProvider({ user, children }: { user: AuthUser; children: ReactNode }) {
  return <AuthorizationContext.Provider value={user}>{children}</AuthorizationContext.Provider>;
}

export function useAuthorization() {
  const user = useContext(AuthorizationContext);
  if (!user) throw new Error("AuthorizationProvider is missing");
  const can = (...roles: string[]) => user.roles.includes("admin") || roles.some((role) => user.roles.includes(role));
  return { user, can };
}
