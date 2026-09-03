/**
 * The only place that talks to the server.
 *
 * Adds the CSRF header, unwraps the uniform error object and reports a lost session
 * once, centrally.
 */

const API_BASE = "/api/v1";
const CSRF_COOKIE = "kehrwoche_csrf";
const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
/**
 * How long a request may take before it counts as failed.
 *
 * Without a limit the browser waits half a minute in a dead spot — long enough for
 * somebody to pocket the phone believing the tick went through.
 */
const TIMEOUT_MS = 8000;
/** A picture takes longer than a JSON call, especially on mobile data. */
const UPLOAD_TIMEOUT_MS = 60000;

/**
 * Error carrying the server's `{ code, message, field?, message_key?, params? }`.
 *
 * `message` is the English developer wording and belongs in the console, never on
 * screen — what a person reads comes from `errorText()` in `error-text.js`.
 */
export class ApiError extends Error {
  constructor(status, error) {
    super(error?.message ?? "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.code = error?.code ?? "internal_error";
    this.field = error?.field ?? null;
    this.messageKey = error?.message_key ?? null;
    this.params = error?.params ?? null;
  }
}

/** The request never reached the server: offline, timed out or connection refused. */
export class NetworkError extends Error {
  constructor(reason) {
    super(reason);
    this.name = "NetworkError";
    /** One of `offline`, `timeout`, `unreachable` — each has its own text. */
    this.reason = reason;
  }
}

let onUnauthorized = () => {};

/** Called whenever the server reports that nobody is signed in. */
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

function csrfToken() {
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function request(method, path, body) {
  // FormData carries its own multipart boundary — setting a content type would break it.
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const headers = { Accept: "application/json" };
  if (body !== undefined && !isForm) {
    headers["Content-Type"] = "application/json";
  }
  if (!SAFE_METHODS.has(method)) {
    // Double submit: mirror the readable cookie into the header.
    const token = csrfToken();
    if (token) {
      headers[CSRF_HEADER] = token;
    }
  }

  if (navigator.onLine === false) {
    // Saying so at once beats a request that cannot succeed anyway.
    throw new NetworkError("offline");
  }

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), isForm ? UPLOAD_TIMEOUT_MS : TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    throw new NetworkError(error?.name === "AbortError" ? "timeout" : "unreachable");
  } finally {
    window.clearTimeout(timeout);
  }

  if (response.status === 204) {
    return null;
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      onUnauthorized();
    }
    throw new ApiError(response.status, payload?.error);
  }
  return payload;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  patch: (path, body) => request("PATCH", path, body ?? {}),
  delete: (path) => request("DELETE", path),
  /** A picture as multipart form data. */
  upload: (path, form) => request("POST", path, form),
  /** For the rare case of a body on DELETE — deleting an account needs the password. */
  request,
};
