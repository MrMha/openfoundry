import type {
  AgentThoughtEvent,
  Approval,
  BrowserFrameEvent,
  CodeDiffEvent,
  PluginMetadata,
  ProbeReport,
  SandboxStatus,
  TaskNode,
} from '../services/types';
export const missionId = '00000000-0000-4000-8000-000000000001';
export const taskId = '00000000-0000-4000-8000-000000000002';
export const task: TaskNode = {
  id: taskId,
  mission_id: missionId,
  department: 'engineer',
  title: 'Build calculator',
  description: 'Implement addition',
  dependencies: [],
  status: 'running',
  output_summary: null,
  requires_approval: true,
};
export const thought: AgentThoughtEvent = {
  type: 'agent_thought',
  schema_version: 1,
  event_id: 'thought-1',
  mission_id: missionId,
  department: 'engineer',
  task_id: taskId,
  timestamp: '2026-01-01T00:00:00Z',
  thought: 'Building calculator',
  level: 'info',
  sequence: 1,
};
export const plugin: PluginMetadata = {
  name: 'engineer',
  role_description: 'Implement and repair code',
  module: 'backend.app.departments.engineer',
  class_name: 'Engineer',
  version: '0.1.0',
  capabilities: ['local_git', 'code_generation'],
  builtin: true,
  enabled: true,
  legacy_replacement: null,
};
export const sandbox: SandboxStatus = {
  configured_mode: 'auto',
  execution_mode: 'guarded',
  isolation_available: false,
  capability_probe_performed: true,
  bwrap_installed: true,
  reason: 'User namespaces blocked',
  max_command_seconds: 60,
  guarded_execution_is_os_isolation: false,
  platform: 'linux',
  checked_at: '2026-01-01T00:00:00Z',
};
export const frame: BrowserFrameEvent = {
  type: 'browser_frame',
  schema_version: 1,
  event_id: 'frame-1',
  mission_id: missionId,
  department: 'qa_self_healer',
  task_id: taskId,
  frame_number: 0,
  screenshot_base64: 'iVBORw0KGgo=',
  screenshot_mime_type: 'image/png',
  current_url: 'http://asgi.local/',
  console_errors: ['Uncaught TypeError: broken'],
  sequence: 2,
};
export const report: ProbeReport = {
  error: null,
  results: [
    {
      path: '/',
      url: 'http://asgi.local/',
      status: 503,
      passed: false,
      failures: ['Expected HTTP 200, received 503', 'Missing Ready text'],
      backend: 'http-asgi-dom',
      warnings: ['No browser runtime'],
      console_errors: ['Uncaught TypeError: broken'],
      frame_path: `qa/${taskId}/round-0/0.png`,
      duration_seconds: 0.1,
    },
  ],
};
export const diff: CodeDiffEvent = {
  type: 'code_diff',
  schema_version: 1,
  event_id: 'diff-1',
  mission_id: missionId,
  department: 'qa_self_healer',
  task_id: taskId,
  file_path: 'app.py',
  sequence: 3,
  unified_diff:
    '--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n',
  test_result: {
    status: 'failed',
    return_code: 1,
    command: ['pytest', '-q'],
    duration_seconds: 0.2,
    output_summary: 'Expected 5, observed -1',
  },
};
export const approval: Approval = {
  approval_id: 'approval-1',
  task_id: taskId,
  mission_id: missionId,
  decision: 'requested',
  reason: null,
  created_at: '2026-01-01T00:00:00Z',
  decided_at: null,
};
