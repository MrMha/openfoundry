import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import eventSchema from './events.schema.json';
import type { StoredEvent } from './types';

const ajv = new Ajv2020({ strict: false, allErrors: false });
addFormats(ajv);
const validate = ajv.compile(eventSchema);
export function parseEvent(payload: string): StoredEvent | null {
  if (payload.length > 8 * 1024 * 1024)
    throw new Error('Event exceeds the backend transport limit');
  const value: unknown = JSON.parse(payload);
  if (typeof value === 'object' && value !== null && 'type' in value && value.type === 'heartbeat')
    return null;
  if (
    !validate(value) ||
    typeof value !== 'object' ||
    value === null ||
    !('sequence' in value) ||
    !Number.isSafeInteger(value.sequence) ||
    Number(value.sequence) < 1
  ) {
    throw new Error('The server sent an incompatible War Room event');
  }
  return value as StoredEvent;
}

export type ConnectionState =
  'connecting' | 'live' | 'reconnecting' | 'unauthorized' | 'offline' | 'error';
type SocketFactory = (url: string, protocols: string[]) => WebSocket;
interface SocketOptions {
  missionId: string;
  token: string;
  after?: number;
  onEvent: (event: StoredEvent) => void;
  onState: (state: ConnectionState) => void;
  onError?: (message: string) => void;
  factory?: SocketFactory;
  origin?: string;
}

export class WarRoomSocket {
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private watchdog: ReturnType<typeof setTimeout> | undefined;
  private stopped = true;
  private attempts = 0;
  private cursor: number;
  constructor(private readonly options: SocketOptions) {
    this.cursor = options.after ?? 0;
  }
  get lastSequence(): number {
    return this.cursor;
  }
  start(): void {
    if (this.stopped) {
      this.stopped = false;
      this.connect();
    }
  }
  stop(): void {
    this.stopped = true;
    clearTimeout(this.retry);
    clearTimeout(this.watchdog);
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.onmessage = null;
      this.socket.onopen = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }
  private armWatchdog(): void {
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => this.socket?.close(4000, 'Heartbeat timeout'), 45000);
  }
  private connect(): void {
    if (this.stopped) return;
    this.options.onState(this.attempts ? 'reconnecting' : 'connecting');
    const url = new URL(
      `/ws/missions/${encodeURIComponent(this.options.missionId)}`,
      this.options.origin ?? window.location.origin,
    );
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('after', String(this.cursor));
    const protocols = ['openfoundry'];
    if (this.options.token) protocols.push(`bearer.${this.options.token}`);
    try {
      if (this.options.token && !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(this.options.token)) {
        throw new Error(
          'Browser WebSocket authentication requires an ASCII operator token without spaces or separators',
        );
      }
      const ws = (
        this.options.factory ?? ((address, protocols) => new WebSocket(address, protocols))
      )(url.toString(), protocols);
      this.socket = ws;
      ws.onopen = () => {
        this.options.onState('live');
        this.armWatchdog();
      };
      ws.onmessage = (message: MessageEvent<unknown>) => {
        this.armWatchdog();
        try {
          if (typeof message.data !== 'string') throw new Error('Expected a text event');
          const event = parseEvent(message.data);
          if (event === null) return;
          if (event.mission_id !== this.options.missionId)
            throw new Error('Event belongs to another mission');
          if (event.sequence <= this.cursor) return;
          this.options.onEvent(event);
          this.cursor = event.sequence;
          this.attempts = 0;
        } catch (error) {
          this.stop();
          this.options.onState('error');
          this.options.onError?.(error instanceof Error ? error.message : 'Invalid event');
        }
      };
      ws.onerror = () => this.options.onState('offline');
      ws.onclose = (event) => {
        clearTimeout(this.watchdog);
        if (this.stopped) return;
        if (event.code === 1008 || event.code === 4401 || event.code === 4403) {
          this.stop();
          this.options.onState('unauthorized');
          return;
        }
        this.options.onState('reconnecting');
        const delay = Math.min(15000, 500 * 2 ** Math.min(this.attempts++, 5));
        this.retry = setTimeout(() => this.connect(), delay + Math.random() * delay * 0.2);
      };
    } catch (error) {
      this.stop();
      this.options.onState('error');
      this.options.onError?.(error instanceof Error ? error.message : 'Unable to open socket');
    }
  }
}
