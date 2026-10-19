import { Shield } from 'lucide-react';
import { canonical, departmentName } from './WarRoomGraph';
import type {
  AgentThoughtEvent,
  PluginMetadata,
  SandboxStatus,
  TaskNode,
  TaskStatus,
} from '../services/types';

interface Props {
  department: string;
  plugin?: PluginMetadata;
  catalogLoaded: boolean;
  tasks: TaskNode[];
  thoughts: AgentThoughtEvent[];
  sandbox: SandboxStatus | null;
  onSelectTask: (id: string) => void;
}
const priority: TaskStatus[] = [
  'running',
  'awaiting_approval',
  'failed',
  'pending',
  'cancelled',
  'skipped',
  'completed',
];
export function DepartmentCard({
  department,
  plugin,
  catalogLoaded,
  tasks,
  thoughts,
  sandbox,
  onSelectTask,
}: Props) {
  const owned = tasks.filter((task) => canonical(task.department) === department);
  const active = [...owned].sort(
    (a, b) => priority.indexOf(a.status) - priority.indexOf(b.status),
  )[0];
  const status = active?.status ?? 'pending';
  const recent = thoughts
    .filter((thought) => canonical(thought.department) === department)
    .slice(-3);
  const registered = catalogLoaded && Boolean(plugin);
  const title = departmentName(department);
  return (
    <article className={`department-card department-${status}`} aria-label={`${title} department`}>
      <div className="department-card-top">
        <span className="department-avatar">
          {department === 'chief_of_staff'
            ? 'CS'
            : department === 'qa_self_healer'
              ? 'QA'
              : department.slice(0, 2).toUpperCase()}
        </span>
        <span className={`status-dot status-${status}`} aria-hidden="true" />
      </div>
      <h3>{title}</h3>
      <span className={`small status-${status}`}>
        {plugin && !plugin.enabled
          ? 'Disabled'
          : !registered && catalogLoaded
            ? 'Not registered'
            : active
              ? status.replaceAll('_', ' ')
              : 'Standing by'}
      </span>
      {active && (
        <button
          className="department-task"
          onClick={() => onSelectTask(active.id)}
          title={active.description}
        >
          {active.title}
          <span>
            {owned.filter((task) => task.status === 'completed').length}/{owned.length} done
          </span>
        </button>
      )}
      {recent.length ? (
        <ol className="department-thoughts" aria-label={`${title} recent thoughts`}>
          {recent.map((thought, index) => (
            <li key={thought.event_id ?? index} className={`thought-${thought.level ?? 'info'}`}>
              <time dateTime={thought.timestamp}>
                {thought.timestamp
                  ? new Date(thought.timestamp).toLocaleTimeString([], { hour12: false })
                  : '—'}
              </time>
              <p>{thought.thought}</p>
            </li>
          ))}
        </ol>
      ) : (
        <p>{plugin?.role_description ?? 'Waiting for mission updates.'}</p>
      )}
      <div
        className="department-sandbox"
        title={
          sandbox
            ? `${sandbox.reason ?? 'Namespace probe succeeded'} · checked ${sandbox.checked_at}`
            : 'Waiting for GET /api/plugins'
        }
      >
        <Shield size={11} />
        <span>
          Sandbox: <strong>{sandbox?.execution_mode ?? 'unknown'}</strong>
        </span>
      </div>
      {sandbox && (
        <span className="sandbox-measured">
          {sandbox.capability_probe_performed
            ? 'Measured capability'
            : 'Configured mode · no namespace probe'}
        </span>
      )}
    </article>
  );
}
