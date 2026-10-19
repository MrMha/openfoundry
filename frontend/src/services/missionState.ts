import type {
  AgentThoughtEvent,
  BrowserFrameEvent,
  CodeDiffEvent,
  MissionDetail,
  StoredEvent,
} from './types';
import { advanceHealing, waitingHealing, type HealingProgress } from './healingState';
export interface MissionState {
  missionId: string | null;
  mission: MissionDetail | null;
  thoughts: AgentThoughtEvent[];
  frame: BrowserFrameEvent | null;
  diffs: CodeDiffEvent[];
  healing: Record<string, HealingProgress>;
}
export const emptyState: MissionState = {
  missionId: null,
  mission: null,
  thoughts: [],
  frame: null,
  diffs: [],
  healing: {},
};
export type MissionAction =
  | { type: 'reset'; missionId?: string }
  | { type: 'snapshot'; mission: MissionDetail }
  | { type: 'event'; event: StoredEvent };
export function missionReducer(state: MissionState, action: MissionAction): MissionState {
  if (action.type === 'reset') return { ...emptyState, missionId: action.missionId ?? null };
  if (action.type === 'snapshot') {
    if (state.missionId && state.missionId !== action.mission.id) return state;
    if (state.mission && state.mission.last_event_id > action.mission.last_event_id) return state;
    return { ...state, missionId: action.mission.id, mission: action.mission };
  }
  const event = action.event;
  if (state.missionId && event.mission_id !== state.missionId) return state;
  let next = state;
  if (
    'task_id' in event &&
    event.task_id &&
    'department' in event &&
    ['qa_self_healer', 'qa'].includes(event.department ?? '')
  ) {
    const progress = advanceHealing(state.healing[event.task_id] ?? waitingHealing, event);
    next = { ...next, healing: { ...state.healing, [event.task_id]: progress } };
  }
  if (event.type === 'agent_thought')
    next = { ...next, thoughts: [...state.thoughts.slice(-79), event] };
  if (event.type === 'browser_frame') next = { ...next, frame: event };
  if (event.type === 'code_diff') next = { ...next, diffs: [...state.diffs.slice(-5), event] };
  if (!next.mission || event.sequence <= next.mission.last_event_id) return next;
  let mission = { ...next.mission, last_event_id: event.sequence };
  if (event.type === 'mission_status_change')
    mission = {
      ...mission,
      status: event.status,
      title: event.title,
      goal: event.goal,
      last_error: event.error ?? null,
    };
  if (event.type === 'task_status_change') {
    const old = mission.tasks.find((task) => task.id === event.task_id);
    const task = {
      id: event.task_id,
      mission_id: event.mission_id,
      department: event.department,
      title: event.title ?? old?.title ?? 'Task',
      description: event.description ?? old?.description ?? '',
      dependencies: event.dependencies ?? old?.dependencies ?? [],
      status: event.status,
      output_summary: event.output_summary ?? null,
      requires_approval: event.requires_approval ?? false,
    };
    mission = { ...mission, tasks: [...mission.tasks.filter((item) => item.id !== task.id), task] };
  }
  if (event.type === 'approval_request' && event.task_id) {
    mission = {
      ...mission,
      approvals: [
        ...mission.approvals.filter((item) => item.approval_id !== event.approval_id),
        {
          approval_id: event.approval_id!,
          task_id: event.task_id,
          mission_id: event.mission_id,
          decision: 'requested',
          reason: null,
          created_at: event.timestamp!,
          decided_at: null,
        },
      ],
    };
  }
  if (event.type === 'approval_decision')
    mission = {
      ...mission,
      approvals: mission.approvals.map((item) =>
        item.approval_id === event.approval_id
          ? {
              ...item,
              decision: event.decision,
              reason: event.reason ?? null,
              decided_at: event.timestamp!,
            }
          : item,
      ),
    };
  return { ...next, mission };
}
