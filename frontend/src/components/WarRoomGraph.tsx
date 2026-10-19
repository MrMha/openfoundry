import { Maximize2, Minus, Plus, Workflow } from 'lucide-react';
import { useId, useMemo, useRef, useState } from 'react';
import type { TaskNode, TaskStatus } from '../services/types';

export const DEPARTMENTS = [
  { id: 'chief_of_staff', name: 'Chief of Staff', role: 'Plan & coordinate', short: 'CS' },
  { id: 'researcher', name: 'Research', role: 'Find the evidence', short: 'RE' },
  { id: 'engineer', name: 'Engineering', role: 'Build & repair', short: 'EN' },
  { id: 'qa_self_healer', name: 'QA & Self-Healing', role: 'Verify & improve', short: 'QA' },
  { id: 'growth_career', name: 'Growth & Career', role: 'Prepare for launch', short: 'GR' },
];
const aliases: Record<string, string> = {
  research: 'researcher',
  engineering: 'engineer',
  qa: 'qa_self_healer',
  growth: 'growth_career',
};
export const canonical = (name: string) => aliases[name] ?? name;
export const departmentName = (id: string) =>
  DEPARTMENTS.find((item) => item.id === canonical(id))?.name ?? id.replaceAll('_', ' ');
const tones: Record<TaskStatus, string> = {
  pending: '#64748b',
  running: '#10B981',
  awaiting_approval: '#F59E0B',
  completed: '#10B981',
  failed: '#F43F5E',
  skipped: '#64748b',
  cancelled: '#64748b',
};
export function layoutTasks(tasks: TaskNode[]) {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const ranks = new Map<string, number>();
  const visit = (id: string, visiting: Set<string>): number => {
    if (ranks.has(id)) return ranks.get(id)!;
    if (visiting.has(id)) return 0;
    const task = byId.get(id);
    if (!task) return -1;
    const next = new Set(visiting).add(id);
    const rank = Math.max(0, ...task.dependencies.map((dependency) => visit(dependency, next) + 1));
    ranks.set(id, rank);
    return rank;
  };
  tasks.forEach((task) => visit(task.id, new Set()));
  const rows = new Map<number, number>();
  return [...tasks]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((task) => {
      const rank = ranks.get(task.id) ?? 0;
      const row = rows.get(rank) ?? 0;
      rows.set(rank, row + 1);
      return { task, x: 30 + rank * 252, y: 60 + row * 146 };
    });
}
interface Props {
  tasks: TaskNode[];
  selected: string | null;
  onSelect: (id: string) => void;
}
export function WarRoomGraph({ tasks, selected, onSelect }: Props) {
  const positions = useMemo(() => layoutTasks(tasks), [tasks]);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; originX: number; originY: number } | null>(null);
  const id = useId().replaceAll(':', '');
  const width = Math.max(1220, ...positions.map((node) => node.x + 240));
  const height = Math.max(280, ...positions.map((node) => node.y + 150));
  const selectedTask = tasks.find((task) => task.id === selected);
  return (
    <section className="panel graph-panel" aria-label="Mission dependency graph">
      <div className="panel-heading">
        <div>
          <Workflow size={17} />
          <h2>Mission topology</h2>
          <span className="tag">LIVE DAG</span>
        </div>
        <div className="graph-controls">
          <button
            aria-label="Zoom out"
            onClick={() => setZoom((value) => Math.max(0.5, value - 0.15))}
          >
            <Minus size={15} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            aria-label="Zoom in"
            onClick={() => setZoom((value) => Math.min(2.5, value + 0.15))}
          >
            <Plus size={15} />
          </button>
          <button
            aria-label="Reset graph view"
            onClick={() => {
              setZoom(1);
              setPan({ x: 0, y: 0 });
            }}
          >
            <Maximize2 size={15} />
          </button>
        </div>
      </div>
      <div className="graph-viewport">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label="Interactive task dependencies"
          onPointerDown={(event) => {
            if ((event.target as Element).closest('[role="button"]')) return;
            drag.current = { x: event.clientX, y: event.clientY, originX: pan.x, originY: pan.y };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (drag.current) {
              const scale = width / event.currentTarget.getBoundingClientRect().width;
              setPan({
                x: drag.current.originX + (event.clientX - drag.current.x) * scale,
                y: drag.current.originY + (event.clientY - drag.current.y) * scale,
              });
            }
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          <defs>
            <pattern id={`dots-${id}`} width="20" height="20" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="0.7" fill="#253146" />
            </pattern>
            <marker
              id={`arrow-${id}`}
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 8 4 L 0 8 z" fill="context-stroke" />
            </marker>
          </defs>
          <rect width="100%" height="100%" fill={`url(#dots-${id})`} />
          <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {positions.flatMap((node) =>
              node.task.dependencies.map((dependency) => {
                const from = positions.find((candidate) => candidate.task.id === dependency);
                if (!from) return null;
                const active = node.task.status === 'running';
                const color =
                  active || (from.task.status === 'completed' && node.task.status === 'completed')
                    ? '#10B981'
                    : node.task.status === 'awaiting_approval'
                      ? '#F59E0B'
                      : '#334155';
                return (
                  <path
                    key={`${dependency}-${node.task.id}`}
                    data-testid="dependency-edge"
                    className={active ? 'active-edge' : ''}
                    d={`M ${from.x + 210} ${from.y + 53} C ${from.x + 235} ${from.y + 53}, ${node.x - 25} ${node.y + 53}, ${node.x} ${node.y + 53}`}
                    fill="none"
                    stroke={color}
                    strokeWidth="2"
                    markerEnd={`url(#arrow-${id})`}
                  />
                );
              }),
            )}
            {positions.map(({ task, x, y }) => (
              <g
                key={task.id}
                transform={`translate(${x} ${y})`}
                role="button"
                tabIndex={0}
                aria-label={`${task.title}: ${task.status.replaceAll('_', ' ')}`}
                aria-pressed={selected === task.id}
                onClick={() => onSelect(task.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(task.id);
                  }
                }}
                className="graph-node"
              >
                <rect
                  width="210"
                  height="110"
                  rx="10"
                  fill="#111827"
                  stroke={
                    selected === task.id
                      ? '#e2e8f0'
                      : task.status === 'pending'
                        ? '#263247'
                        : tones[task.status]
                  }
                  strokeWidth={selected === task.id ? 2 : 1}
                />
                <rect
                  x="14"
                  y="15"
                  width="29"
                  height="29"
                  rx="7"
                  fill={`${tones[task.status]}18`}
                />
                <text
                  x="28"
                  y="34"
                  textAnchor="middle"
                  fill={tones[task.status]}
                  fontSize="11"
                  fontWeight="700"
                >
                  {DEPARTMENTS.find((item) => item.id === canonical(task.department))?.short ??
                    'AI'}
                </text>
                <text x="53" y="27" fill="#e2e8f0" fontSize="12" fontWeight="600">
                  {departmentName(task.department)}
                </text>
                <text x="53" y="43" fill="#94a3b8" fontSize="10">
                  {task.title.length > 22 ? task.title.slice(0, 21) + '…' : task.title}
                </text>
                <line x1="14" x2="196" y1="59" y2="59" stroke="#253146" />
                <circle
                  cx="21"
                  cy="82"
                  r="3"
                  fill={tones[task.status]}
                  className={task.status === 'running' ? 'pulse' : ''}
                />
                <text x="32" y="86" fontSize="11" fill={tones[task.status]}>
                  {task.status.replaceAll('_', ' ')}
                </text>
                {task.requires_approval && (
                  <text x="191" y="86" textAnchor="end" fill="#F59E0B" fontSize="10">
                    GATE
                  </text>
                )}
              </g>
            ))}
            {!tasks.length &&
              DEPARTMENTS.map((department, index) => (
                <g key={department.id} transform={`translate(${30 + index * 240} 70)`}>
                  <rect
                    width="208"
                    height="100"
                    rx="10"
                    fill="#111827"
                    stroke="#253146"
                    strokeDasharray="5 4"
                  />
                  <text x="18" y="35" fill="#94a3b8" fontSize="13">
                    {department.name}
                  </text>
                  <text x="18" y="60" fill="#64748b" fontSize="11">
                    {department.role}
                  </text>
                  <text x="18" y="82" fill="#64748b" fontSize="10">
                    AWAITING MISSION
                  </text>
                </g>
              ))}
          </g>
        </svg>
      </div>
      <div className="graph-footer">
        <div className="graph-legend">
          <span>
            <i className="bg-emerald-500" />
            Active / complete
          </span>
          <span>
            <i className="bg-amber-500" />
            Approval gate
          </span>
          <span>
            <i className="bg-rose-500" />
            Failed task
          </span>
        </div>
        <span className="small subtle">Drag to pan · select a task</span>
      </div>
      {selectedTask && (
        <div className="task-detail">
          <span className="eyebrow">{departmentName(selectedTask.department)}</span>
          <h3>{selectedTask.title}</h3>
          <p>{selectedTask.description}</p>
          {selectedTask.output_summary && (
            <details>
              <summary>Task output</summary>
              <pre>{selectedTask.output_summary}</pre>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
