const BASE = "/api";

export type AuthUser = {
  id: number;
  email: string;
  display_name: string;
  is_active: boolean;
  must_change_password: boolean;
  roles: string[];
  permissions: string[];
  created_at: string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(method);
  if (unsafe && !readCookie("garment_erp_csrf")) {
    await fetch(BASE + "/auth/csrf", { credentials: "include" });
  }
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (unsafe) {
    const csrf = readCookie("garment_erp_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const res = await fetch(BASE + path, {
    method,
    credentials: "include",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail))
        detail = data.detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
    } catch {
      /* ignore malformed error bodies */
    }
    if (res.status === 401 && path !== "/auth/login" && path !== "/auth/me") {
      window.dispatchEvent(new Event("erp:unauthorized"));
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return null;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
};

export const auth = {
  me: () => api.get<AuthUser>("/auth/me"),
  login: (email: string, password: string) =>
    api.post<AuthUser>("/auth/login", { email, password }),
  logout: () => api.post<void>("/auth/logout"),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.post<void>("/auth/password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
};
