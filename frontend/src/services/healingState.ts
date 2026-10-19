import type { StoredEvent } from './types';

export type HealingPhase = 'waiting' | 'detected' | 'patching' | 'verified' | 'stopped';
export interface HealingProgress {
  phase: HealingPhase;
  bugDetected: boolean;
  patchObserved: boolean;
  failureRounds: number;
  sequence: number;
}
export const waitingHealing: HealingProgress = {
  phase: 'waiting',
  bugDetected: false,
  patchObserved: false,
  failureRounds: 0,
  sequence: 0,
};
const isQA = (name: string) => name === 'qa_self_healer' || name === 'qa';
export function advanceHealing(current: HealingProgress, event: StoredEvent): HealingProgress {
  if (!('department' in event) || !isQA(event.department) || event.sequence <= current.sequence)
    return current;
  let next = { ...current, sequence: event.sequence };
  if (event.type === 'code_diff') {
    if (['failed', 'error', 'timed_out'].includes(event.test_result.status))
      next = {
        ...next,
        phase: 'detected',
        bugDetected: true,
        failureRounds: current.failureRounds + (current.phase === 'detected' ? 0 : 1),
      };
    else if (event.test_result.status === 'not_run' && current.bugDetected)
      next = { ...next, phase: 'patching', patchObserved: true };
    else if (event.test_result.status === 'passed') next.phase = 'verified';
  }
  if (event.type === 'task_status_change') {
    if (event.status === 'completed') next.phase = 'verified';
    if (['failed', 'cancelled', 'skipped'].includes(event.status)) next.phase = 'stopped';
  }
  return next;
}
