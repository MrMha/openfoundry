import { describe, expect, it } from 'vitest';
import { advanceHealing, waitingHealing } from './healingState';
import { missionReducer, emptyState } from './missionState';
import { diff, taskId } from '../test/fixtures';
import type { CodeDiffEvent } from './types';
function update(
  sequence: number,
  status: 'failed' | 'not_run' | 'passed',
): CodeDiffEvent & { sequence: number } {
  return { ...diff, type: 'code_diff', sequence, test_result: { ...diff.test_result, status } };
}
describe('typed self-healing progress', () => {
  it('observes bug detection, committed patch, and passing verification without inferring progress from prose', () => {
    const detected = advanceHealing(waitingHealing, update(3, 'failed'));
    expect(detected.phase).toBe('detected');
    expect(detected.failureRounds).toBe(1);
    const patching = advanceHealing(detected, update(4, 'not_run'));
    expect(patching.phase).toBe('patching');
    const verified = advanceHealing(patching, update(5, 'passed'));
    expect(verified).toMatchObject({ phase: 'verified', bugDetected: true, patchObserved: true });
    expect(advanceHealing(verified, update(4, 'failed'))).toBe(verified);
    expect(advanceHealing(waitingHealing, { ...update(3, 'failed'), department: 'engineer' })).toBe(
      waitingHealing,
    );
  });
  it('retains healing history after the bounded diff buffer evicts old failures', () => {
    let state = emptyState;
    for (let sequence = 1; sequence <= 20; sequence++)
      state = missionReducer(state, {
        type: 'event',
        event: update(sequence, sequence === 1 ? 'failed' : sequence === 2 ? 'not_run' : 'passed'),
      });
    expect(state.diffs).toHaveLength(6);
    expect(state.diffs.every((item) => item.test_result.status === 'passed')).toBe(true);
    expect(state.healing[taskId]).toMatchObject({
      phase: 'verified',
      failureRounds: 1,
      patchObserved: true,
    });
    expect(missionReducer(state, { type: 'reset', missionId: 'another-mission' }).healing).toEqual(
      {},
    );
  });
  it('counts a failed repair as another verification round, and leaves a clean first pass without a fictitious patch', () => {
    let state = advanceHealing(waitingHealing, update(1, 'failed'));
    state = advanceHealing(state, update(2, 'failed'));
    expect(state.failureRounds).toBe(1);
    state = advanceHealing(state, update(3, 'not_run'));
    state = advanceHealing(state, update(4, 'failed'));
    expect(state.failureRounds).toBe(2);
    expect(advanceHealing(waitingHealing, update(1, 'passed'))).toMatchObject({
      phase: 'verified',
      bugDetected: false,
      patchObserved: false,
    });
  });
});
