import axios, { AxiosError, type AxiosRequestConfig } from "axios";

/**
 * Centralised API client.
 *  - Access token: kept in memory only (not readable from storage by injected scripts after reload).
 *  - Refresh token: sessionStorage (cleared when the tab closes). Trade-off documented in SECURITY.md;
 *    the production-grade alternative is an httpOnly SameSite cookie issued by the backend.
 *  - The server is always authoritative: the UI never decides what a user may see.
 */
const REFRESH_KEY = "rag.refresh";
let accessToken: string | null = null;
let onAuthLost: (() => void) | null = null;

export const tokenStore = {
  setTokens(access: string, refresh?: string) {
    accessToken = access;
    if (refresh) {
      try { sessionStorage.setItem(REFRESH_KEY, refresh); } catch { /* storage may be unavailable */ }
    }
  },
  getRefresh(): string | null {
    try { return sessionStorage.getItem(REFRESH_KEY); } catch { return null; }
  },
  clear() {
    accessToken = null;
    try { sessionStorage.removeItem(REFRESH_KEY); } catch { /* ignore */ }
  },
  hasAccess: () => accessToken !== null,
};

export function setAuthLostHandler(fn: () => void) { onAuthLost = fn; }

export const http = axios.create({ baseURL: "/api", timeout: 90_000 });

http.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = tokenStore.getRefresh();
  if (!refresh) return false;
  try {
    const res = await axios.post("/api/auth/refresh", null, { headers: { Authorization: `Bearer ${refresh}` } });
    tokenStore.setTokens(res.data.access_token, res.data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/** Used on page load: the access token lives only in memory, so obtain one up front instead of
 *  firing a request that is certain to 401. Shares the single in-flight refresh (refresh tokens are
 *  single-use, so concurrent callers such as React StrictMode's double effect must not each spend one). */
export async function ensureAccessToken(): Promise<boolean> {
  if (accessToken) return true;
  refreshing ??= refreshTokens().finally(() => { refreshing = null; });
  return refreshing;
}

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
    const isAuthCall = original?.url?.startsWith("/auth/login") || original?.url?.startsWith("/auth/refresh");
    if (error.response?.status === 401 && original && !original._retried && !isAuthCall) {
      original._retried = true;
      refreshing ??= refreshTokens().finally(() => { refreshing = null; });
      if (await refreshing) return http(original);
      tokenStore.clear();
      onAuthLost?.();
    }
    return Promise.reject(error);
  },
);

export interface ApiErrorInfo { status: number | null; code: string; message: string; correlationId?: string }

export function describeError(err: unknown): ApiErrorInfo {
  if (axios.isAxiosError(err)) {
    const body = err.response?.data as { error?: { code?: string; message?: string; correlation_id?: string; details?: { fields?: Record<string, unknown> } } } | undefined;
    if (body?.error) {
      let message = body.error.message ?? "Request failed";
      const fields = body.error.details?.fields;
      if (fields) message += ": " + Object.entries(fields).map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : String(v)}`).join("; ");
      return { status: err.response?.status ?? null, code: body.error.code ?? "error", message, correlationId: body.error.correlation_id };
    }
    if (err.code === "ECONNABORTED") return { status: null, code: "timeout", message: "The request timed out. Please try again." };
    if (!err.response) return { status: null, code: "network", message: "Cannot reach the server. Check your connection and try again." };
    return { status: err.response.status, code: "http_error", message: `Request failed (${err.response.status})` };
  }
  return { status: null, code: "unknown", message: "Something went wrong." };
}
