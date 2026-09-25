const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';
const TOKEN_KEY = 'threads_monitor_token';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  const token = getToken();
  if (token) result.set('Authorization', `Bearer ${token}`);
  return result;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => (typeof item === 'object' && item && 'msg' in item ? String(item.msg) : 'Ошибка запроса'))
        .join('. ');
    }
    if (typeof body.message === 'string') return body.message;
  } catch {
    // Use the status based fallback below for non-JSON responses.
  }
  return response.statusText || `Ошибка запроса (${response.status})`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = authHeaders(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (response.status === 401 && getToken()) window.dispatchEvent(new Event('threads:unauthorized'));
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function apiBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const headers = authHeaders(init.headers);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (response.status === 401 && getToken()) window.dispatchEvent(new Event('threads:unauthorized'));
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return response.blob();
}

export function toQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export function userMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Не удалось выполнить запрос. Попробуйте ещё раз.';
}
