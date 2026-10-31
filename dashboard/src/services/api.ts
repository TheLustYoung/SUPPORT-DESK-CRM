export type Row = Record<string, any>;

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** The error code of a response (`detail`), or the messages of a validation error list. */
function message(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const texts = detail.map(d => String(d?.msg ?? '').replace(/^Value error, /, '')).filter(Boolean);
    if (texts.length) return [...new Set(texts)].join(' · ');
  }
  return JSON.stringify(detail);
}

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (handler: () => void) => { onUnauthorized = handler; };

async function check(response: Response, route: string) {
  if (!response.ok) {
    if (response.status === 401 && route !== 'auth/login') onUnauthorized();
    const payload = await response.json().catch(() => ({detail: `HTTP ${response.status}`}));
    throw new ApiError(response.status, message(payload.detail));
  }
  return response;
}

/** Same-origin JSON API; the session is an HttpOnly cookie, never stored in JS. */
export async function api<T = any>(route: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api/' + route, {
    method, credentials: 'include', headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await check(response, route)).json();
}

/** Multipart upload (the browser sets the boundary itself). */
export async function upload<T = any>(route: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const body = new FormData();
  body.append('file', file);
  Object.entries(fields).forEach(([k, v]) => body.append(k, v));
  const response = await fetch('/api/' + route, {method: 'POST', credentials: 'include', body});
  return (await check(response, route)).json();
}

/** Downloads a file the API sends (exports, attachments, backups) under its own name. */
export async function download(route: string): Promise<void> {
  const response = await check(await fetch('/api/' + route, {credentials: 'include'}), route);
  const name = /filename="?([^"]+)"?/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? 'download';
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement('a'), {href: url, download: decodeURIComponent(name)});
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export const query = (params: Record<string, unknown>) =>
  Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&');
