// Thin typed fetch wrapper. The active RBAC role is sent as X-Role on every
// request (matching the backend's dev auth); the value is kept in localStorage
// and mirrored here so requests always carry the current role.

const BASE = "/api";

let currentRole = localStorage.getItem("erp-role") || "admin";

export function setRole(role: string) {
  currentRole = role;
  localStorage.setItem("erp-role", role);
}
export function getRole() {
  return currentRole;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Role": currentRole,
    },
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
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
};
