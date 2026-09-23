import { decodeApiError } from "./decode";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export interface ApiClientOptions {
  fetchImpl?: typeof fetch;
  onUnauthorized?: () => void;
  timeoutMs?: number;
}

export class ApiClient {
  private readonly fetchImpl: typeof fetch;
  private readonly onUnauthorized: () => void;
  private readonly timeoutMs: number;

  constructor(options: ApiClientOptions = {}) {
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    this.onUnauthorized = options.onUnauthorized ?? (() => undefined);
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.request(path, {
      ...init,
      headers: { Accept: "application/json", ...init.headers },
    });
    const value = await response.json().catch(() => {
      throw new ApiRequestError("The camera returned invalid JSON.", response.status, "invalid_response", false);
    });
    return value as T;
  }

  async blob(path: string, init: RequestInit = {}): Promise<Blob> {
    return (await this.request(path, { ...init, headers: { Accept: "image/jpeg", ...init.headers } })).blob();
  }

  async empty(path: string, init: RequestInit = {}): Promise<void> {
    await this.request(path, { ...init, headers: { Accept: "application/json", ...init.headers } });
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    return this.json<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async postForm<T>(path: string, body: URLSearchParams): Promise<T> {
    return this.json<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  }

  async postText<T>(path: string, body: string): Promise<T> {
    return this.json<T>(path, {
      method: "POST",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body,
    });
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    const timeoutController = new AbortController();
    const timer = globalThis.setTimeout(() => timeoutController.abort("timeout"), this.timeoutMs);
    const forwardAbort = () => timeoutController.abort(init.signal?.reason);
    init.signal?.addEventListener("abort", forwardAbort, { once: true });
    try {
      const headers = new Headers(init.headers);
      const method = (init.method ?? "GET").toUpperCase();
      if (["POST", "PUT", "DELETE"].includes(method)) {
        headers.set("X-Requested-With", "Thingino-WebUI");
      }
      const response = await this.fetchImpl(path, {
        cache: "no-store",
        credentials: "same-origin",
        ...init,
        headers,
        signal: timeoutController.signal,
      });
      if (response.ok) return response;

      const payload = await response.clone().json().catch(() => null);
      const envelope = decodeApiError(payload);
      const code = envelope?.error.code ?? `http_${response.status}`;
      const message = envelope?.error.message ?? `Request failed with status ${response.status}.`;
      if (response.status === 401) this.onUnauthorized();
      throw new ApiRequestError(message, response.status, code, response.status >= 500);
    } catch (error) {
      if (error instanceof ApiRequestError) throw error;
      if (timeoutController.signal.aborted) {
        throw new ApiRequestError("The camera did not respond in time.", 0, "client_timeout", true);
      }
      throw new ApiRequestError(
        error instanceof Error ? error.message : "The camera connection was interrupted.",
        0,
        "network_error",
        true,
      );
    } finally {
      globalThis.clearTimeout(timer);
      init.signal?.removeEventListener("abort", forwardAbort);
    }
  }
}
