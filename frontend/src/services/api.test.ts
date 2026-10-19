import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from './api';

describe('backend REST contract', () => {
  it('uses exact mission/configuration payload, paths, bearer header, and encoded file query', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', { status: 200 }));
    const api = new ApiClient('operator-secret', fetcher);
    await api.create({
      goal: 'Build notes',
      configuration: { provider: 'openai', model: 'openai/gpt-4o-mini', budget_usd: '1.25' },
    });
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe('/api/missions');
    expect(options?.method).toBe('POST');
    expect(new Headers(options?.headers).get('Authorization')).toBe('Bearer operator-secret');
    expect(JSON.parse(String(options?.body))).toEqual({
      goal: 'Build notes',
      configuration: { provider: 'openai', model: 'openai/gpt-4o-mini', budget_usd: '1.25' },
    });
    fetcher.mockResolvedValue(new Response('{}'));
    await api.file('mission-id', 'research/source with space.md');
    expect(fetcher.mock.calls[1][0]).toBe(
      '/api/missions/mission-id/files/content?path=research%2Fsource+with+space.md',
    );
    expect(String(fetcher.mock.calls[1][0])).not.toContain('secret');
  });
  it('uses a body-free demo POST, REST-only approval paths and supports key-free local access', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response('{}'));
    const api = new ApiClient('', fetcher);
    await api.demo();
    await api.decide('m', 't', 'approve');
    await api.decide('m', 't', 'reject', 'Change scope');
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
      '/api/missions/demo',
      '/api/missions/m/tasks/t/approve',
      '/api/missions/m/tasks/t/reject',
    ]);
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined();
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has('Authorization')).toBe(false);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ reason: 'Change scope' });
  });
  it('preserves authentication errors and handles validation/proxy errors', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Token required' }), { status: 401 }),
      );
    const api = new ApiClient('', fetcher);
    await expect(api.runtime()).rejects.toEqual(new ApiError(401, 'Token required'));
    fetcher.mockResolvedValue(new Response('upstream error', { status: 502 }));
    await expect(api.plugins()).rejects.toThrow('Request failed (502)');
    fetcher.mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ msg: 'invalid' }] }), { status: 422 }),
    );
    await expect(api.create({ goal: 'test' })).rejects.toThrow('Invalid request');
  });
});
