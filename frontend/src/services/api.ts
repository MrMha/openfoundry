import type {
  Approval,
  Artifact,
  FileContent,
  Mission,
  MissionCreate,
  MissionDetail,
  PluginCatalog,
  Runtime,
  Blueprint,
} from './types';

const TOKEN_KEY = 'openfoundry.operator-token';
export const MISSION_KEY = 'openfoundry.mission';
export function savedValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? '';
  } catch {
    return '';
  }
}
export function saveValue(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* Private browser mode: retain session state. */
  }
}
export const loadToken = () => savedValue(TOKEN_KEY);
export const saveToken = (token: string) => saveValue(TOKEN_KEY, token.trim());

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class ApiClient {
  constructor(
    private readonly token: string,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set('Accept', 'application/json');
    if (options.body) headers.set('Content-Type', 'application/json');
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    const response = await this.fetcher(`/api${path}`, {
      ...options,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const payload: unknown = await response.json();
        if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
          const detail = payload.detail;
          message =
            typeof detail === 'string'
              ? detail
              : 'Invalid request. Check the mission configuration.';
        }
      } catch {
        /* Proxy errors can have non-JSON bodies. */
      }
      throw new ApiError(response.status, message);
    }
    return response.json() as Promise<T>;
  }

  runtime(signal?: AbortSignal): Promise<Runtime> {
    return this.request('/runtime', { signal });
  }
  blueprints(signal?: AbortSignal): Promise<Blueprint[]> {
    return this.request('/blueprints', { signal });
  }
  plugins(signal?: AbortSignal): Promise<PluginCatalog> {
    return this.request('/plugins', { signal });
  }
  missions(signal?: AbortSignal): Promise<Mission[]> {
    return this.request('/missions?limit=50&offset=0', { signal });
  }
  mission(id: string, signal?: AbortSignal): Promise<MissionDetail> {
    return this.request(`/missions/${encodeURIComponent(id)}`, { signal });
  }
  create(body: MissionCreate): Promise<Mission> {
    return this.request('/missions', { method: 'POST', body: JSON.stringify(body) });
  }
  demo(): Promise<Mission> {
    return this.request('/missions/demo', { method: 'POST' });
  }
  decide(
    id: string,
    task: string,
    decision: 'approve' | 'reject',
    reason?: string,
  ): Promise<Approval> {
    return this.request(
      `/missions/${encodeURIComponent(id)}/tasks/${encodeURIComponent(task)}/${decision}`,
      { method: 'POST', body: JSON.stringify({ reason: reason ?? null }) },
    );
  }
  artifacts(id: string, signal?: AbortSignal): Promise<Artifact[]> {
    return this.request(`/missions/${encodeURIComponent(id)}/files`, { signal });
  }
  file(id: string, path: string, signal?: AbortSignal): Promise<FileContent> {
    return this.request(
      `/missions/${encodeURIComponent(id)}/files/content?${new URLSearchParams({ path })}`,
      { signal },
    );
  }
}
