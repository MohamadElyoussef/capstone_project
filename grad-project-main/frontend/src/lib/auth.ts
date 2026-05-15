export type UserRole = "ADMIN";

const TOKEN_STORAGE_KEY = "uniclass.access_token";
const ROLE_STORAGE_KEY = "uniclass.user_role";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function getStoredRole(): UserRole | null {
  const value = localStorage.getItem(ROLE_STORAGE_KEY);
  if (value === "ADMIN") {
    return value;
  }
  return null;
}

export function setStoredRole(role: UserRole): void {
  localStorage.setItem(ROLE_STORAGE_KEY, role);
}

export function clearStoredRole(): void {
  localStorage.removeItem(ROLE_STORAGE_KEY);
}

export function clearAuthStorage(): void {
  clearStoredToken();
  clearStoredRole();
}
