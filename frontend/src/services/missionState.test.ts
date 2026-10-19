import { describe, expect, it } from 'vitest';
import examples from '../test/events.json';
import { emptyState, missionReducer } from './missionState';
import { parseEvent } from './warRoomSocket';
import type { MissionDetail } from './types';
const mission: MissionDetail = {
  id: examples[0].mission_id,
  title: 'Calculator',
  goal: 'Add',
  status: 'completed',
  total_cost_usd: '0',
  reserved_cost_usd: '0',
  created_at: '2026-01-01T00:00:00Z',
  last_event_id: 20,
  last_error: null,
  tasks: [],
  artifacts: [],
  approvals: [],
  execution: { mode: 'demo', budget_usd: '2' },
};
describe('snapshot and replay synchronization', () => {
  it('replays evidence while refusing to regress a newer mission snapshot', () => {
    let state = missionReducer(emptyState, { type: 'snapshot', mission });
    for (const example of examples)
      state = missionReducer(state, { type: 'event', event: parseEvent(JSON.stringify(example))! });
    expect(state.mission?.status).toBe('completed');
    expect(state.mission?.tasks).toEqual([]);
    expect(state.thoughts).toHaveLength(1);
    expect(state.frame).not.toBeNull();
    expect(state.diffs).toHaveLength(1);
    const old = missionReducer(state, {
      type: 'snapshot',
      mission: { ...mission, last_event_id: 1, status: 'running' },
    });
    expect(old).toBe(state);
  });
  it('updates from new task descriptors, approval requests and decisions without accepting another mission', () => {
    let state = missionReducer(emptyState, {
      type: 'snapshot',
      mission: { ...mission, last_event_id: 0, status: 'running' },
    });
    for (const example of examples)
      state = missionReducer(state, { type: 'event', event: parseEvent(JSON.stringify(example))! });
    expect(state.mission?.tasks[0].title).toBe('Build calculator');
    expect(state.mission?.approvals[0].decision).toBe('approved');
    const event = {
      ...parseEvent(JSON.stringify(examples[2]))!,
      mission_id: 'another-mission',
      sequence: 99,
    };
    expect(missionReducer(state, { type: 'event', event })).toBe(state);
  });
});

it('ignores a delayed approval snapshot after the user switches missions', () => {
  const state = missionReducer(emptyState, { type: 'reset', missionId: 'new-mission' });
  expect(missionReducer(state, { type: 'snapshot', mission })).toBe(state);
  expect(
    missionReducer(state, { type: 'event', event: parseEvent(JSON.stringify(examples[2]))! }),
  ).toBe(state);
});
