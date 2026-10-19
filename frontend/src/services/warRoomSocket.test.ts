import { afterEach, describe, expect, it, vi } from 'vitest';
import examples from '../test/events.json';
import { parseEvent, WarRoomSocket } from './warRoomSocket';

const missionId = '00000000-0000-4000-8000-000000000001';
class FakeSocket {
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn(() => this.onclose?.({ code: 1000 }));
  message(value: unknown) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }
}
afterEach(() => vi.useRealTimers());
describe('real Pydantic event schema and reconnect contract', () => {
  it('validates all seven backend-serialized variants and distinguishes heartbeat controls', () => {
    expect(examples.map((event) => parseEvent(JSON.stringify(event))?.type)).toEqual(
      examples.map((event) => event.type),
    );
    expect(parseEvent(JSON.stringify({ type: 'heartbeat', cursor: 9 }))).toBeNull();
    expect(() => parseEvent(JSON.stringify({ ...examples[2], thought: 42 }))).toThrow(
      'incompatible',
    );
    expect(() => parseEvent(JSON.stringify({ ...examples[2], schema_version: 2 }))).toThrow(
      'incompatible',
    );
    expect(() => parseEvent(JSON.stringify({ ...examples[2], sequence: null }))).toThrow(
      'incompatible',
    );
  });
  it('authenticates with subprotocols, deduplicates replay, ignores heartbeat cursor, and resumes after last delivered sequence', () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    const sockets: FakeSocket[] = [];
    const factory = vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
    const onEvent = vi.fn();
    const onState = vi.fn();
    const client = new WarRoomSocket({
      missionId,
      token: 'secret-token',
      onEvent,
      onState,
      factory,
      origin: 'https://foundry.example',
    });
    client.start();
    sockets[0].onopen?.();
    expect(factory).toHaveBeenCalledWith(`wss://foundry.example/ws/missions/${missionId}?after=0`, [
      'openfoundry',
      'bearer.secret-token',
    ]);
    sockets[0].message(examples[0]);
    sockets[0].message(examples[0]);
    sockets[0].message({ type: 'heartbeat', cursor: 999 });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(client.lastSequence).toBe(1);
    sockets[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(500);
    expect(factory.mock.calls).toHaveLength(2);
    expect(factory.mock.calls[1]).toEqual([
      `wss://foundry.example/ws/missions/${missionId}?after=1`,
      ['openfoundry', 'bearer.secret-token'],
    ]);
    sockets[1].message(examples[2]);
    expect(client.lastSequence).toBe(3);
    client.stop();
    vi.runOnlyPendingTimers();
    expect(factory).toHaveBeenCalledTimes(2);
  });
  it('stops on policy/authentication failure and rejects cross-mission or invalid messages', () => {
    vi.useFakeTimers();
    const socket = new FakeSocket();
    const onEvent = vi.fn();
    const onState = vi.fn();
    const onError = vi.fn();
    const factory = vi.fn(() => socket as unknown as WebSocket);
    const client = new WarRoomSocket({ missionId, token: '', onEvent, onState, onError, factory });
    client.start();
    socket.onclose?.({ code: 1008 });
    vi.runOnlyPendingTimers();
    expect(onState).toHaveBeenLastCalledWith('unauthorized');
    expect(factory).toHaveBeenCalledTimes(1);
    const second = new WarRoomSocket({ missionId, token: '', onEvent, onState, onError, factory });
    second.start();
    socket.message({ ...examples[0], mission_id: '00000000-0000-4000-8000-000000000099' });
    expect(onError).toHaveBeenCalledWith('Event belongs to another mission');
    expect(onEvent).not.toHaveBeenCalled();
    second.stop();
  });
});

it('rejects tokens that cannot be sent in browser subprotocols without exposing their value', () => {
  const factory = vi.fn();
  const onError = vi.fn();
  const client = new WarRoomSocket({
    missionId,
    token: 'private token/value',
    onEvent: vi.fn(),
    onState: vi.fn(),
    onError,
    factory,
  });
  client.start();
  expect(factory).not.toHaveBeenCalled();
  expect(onError.mock.calls[0][0]).toContain('ASCII operator token');
  expect(onError.mock.calls[0][0]).not.toContain('private token/value');
});
