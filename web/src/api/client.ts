const KEY = "opds-auth";

export function setAuth(username: string, password: string) {
  sessionStorage.setItem(KEY, btoa(`${username}:${password}`));
}

export function clearAuth() {
  sessionStorage.removeItem(KEY);
}

export function hasAuth(): boolean {
  return !!sessionStorage.getItem(KEY);
}

export function authHeader(): Record<string, string> {
  const token = sessionStorage.getItem(KEY);
  return token ? { Authorization: `Basic ${token}` } : {};
}

export async function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const auth = authHeader();
  if (auth.Authorization) headers.set("Authorization", auth.Authorization);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401) {
    clearAuth();
    if (!location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    throw new Error("未授权");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  const ctype = resp.headers.get("content-type") || "";
  if (ctype.includes("application/json")) return resp.json();
  return resp;
}
