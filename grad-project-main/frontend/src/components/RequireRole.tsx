import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { getStoredRole, getStoredToken, type UserRole } from "../lib/auth";

export function RequireRole({
  role,
  children,
}: {
  role: UserRole;
  children: ReactNode;
}) {
  const token = getStoredToken();
  const storedRole = getStoredRole();

  if (!token || !storedRole) {
    return <Navigate to="/login" replace />;
  }

  if (storedRole !== role) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
