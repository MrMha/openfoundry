import {
  Activity,
  ArrowUpRight,
  ChevronRight,
  Circle,
  Radio,
  Shield,
  Terminal,
  TriangleAlert,
  X,
  Zap,
  Maximize2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { ApprovalModal } from './components/ApprovalModal';
import { CodeDiffInspector } from './components/CodeDiffInspector';
import { CommandBar } from './components/CommandBar';
import type { Blueprint, BlueprintSelection } from './services/types';
import { DepartmentCard } from './components/DepartmentCard';
import { LiveBrowserViewer } from './components/LiveBrowserViewer';
import { DEPARTMENTS, WarRoomGraph, canonical, departmentName } from './components/WarRoomGraph';
import {
  ApiClient,
  ApiError,
  MISSION_KEY,
  loadToken,
  saveToken,
  savedValue,
  saveValue,
} from './services/api';
import { waitingHealing } from './services/healingState';
import { emptyState, missionReducer } from './services/missionState';
import { WarRoomSocket, type ConnectionState } from './services/warRoomSocket';
import type { Mission, MissionConfiguration, PluginCatalog, Runtime } from './services/types';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed';
}
const terminal = new Set(['completed', 'failed', 'cancelled']);
export default function App() {
  const [token, setToken] = useState(loadToken);
  const [credentialsVersion, setCredentialsVersion] = useState(0);
  const api = useMemo(() => new ApiClient(token), [token, credentialsVersion]);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [catalog, setCatalog] = useState<PluginCatalog | null>(null);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionId, setMissionId] = useState(() => savedValue(MISSION_KEY));
  const currentMission = useRef(missionId);
  const [state, dispatch] = useReducer(missionReducer, emptyState);
  const [connection, setConnection] = useState<ConnectionState>('offline');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const decisionLock = useRef(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [focusMode, setFocusMode] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setRuntime(null);
    setCatalog(null);
    setError('');
    void Promise.all([
      api.runtime(controller.signal),
      api.plugins(controller.signal),
      api.missions(controller.signal),
      api.blueprints(controller.signal),
    ])
      .then(([configuration, plugins, list, templates]) => {
        if (controller.signal.aborted) return;
        setBlueprints(templates);
        setRuntime(configuration);
        setCatalog(plugins);
        setMissions(list);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setError(
            error instanceof ApiError && error.status === 401
              ? 'Authentication required. Open Connect backend and enter your operator token.'
              : errorMessage(error),
          );
      });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (!runtime) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const value = await api.plugins(controller.signal);
        if (!controller.signal.aborted) setCatalog(value);
      } catch {
        /* Keep the last measured result and its checked_at timestamp. */
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => {
          void refresh();
        }, 30000);
    }
    timer = setTimeout(() => {
      void refresh();
    }, 30000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [api, runtime]);

  useEffect(() => {
    currentMission.current = missionId;
    dispatch({ type: 'reset', missionId });
    setSelected(null);
    setDismissed([]);
    setReviewId(null);
    setApprovalError(null);
    setConnection('offline');
    if (!missionId || !runtime) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const socket = new WarRoomSocket({
      missionId,
      token,
      onEvent: (event) => dispatch({ type: 'event', event }),
      onState: setConnection,
      onError: setError,
    });
    async function poll(initial = false) {
      try {
        const mission = await api.mission(missionId, controller.signal);
        if (controller.signal.aborted) return;
        dispatch({ type: 'snapshot', mission });
        setMissions((current) =>
          [mission, ...current.filter((item) => item.id !== mission.id)].slice(0, 50),
        );
        if (initial) socket.start();
        if (!terminal.has(mission.status))
          timer = setTimeout(() => {
            void poll();
          }, 1200);
      } catch (error) {
        if (controller.signal.aborted) return;
        setError(errorMessage(error));
        if (!(error instanceof ApiError && [401, 404].includes(error.status)))
          timer = setTimeout(() => {
            void poll(initial);
          }, 3000);
      }
    }
    void poll(true);
    return () => {
      controller.abort();
      clearTimeout(timer);
      socket.stop();
    };
  }, [api, missionId, runtime, token]);

  const changeMission = useCallback((id: string) => {
    currentMission.current = id;
    setMissionId(id);
    saveValue(MISSION_KEY, id);
    setError('');
  }, []);
  async function launch(
    demo: boolean,
    goal = '',
    configuration: MissionConfiguration = {},
    blueprint: BlueprintSelection = {},
  ) {
    setBusy(true);
    setError('');
    try {
      const mission = demo
        ? await api.demo()
        : await api.create({ goal, configuration, ...blueprint });
      changeMission(mission.id);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function decide(taskId: string, decision: 'approve' | 'reject') {
    if (decisionLock.current) return;
    decisionLock.current = true;
    const requestedMission = missionId;
    setDeciding(taskId);
    setApprovalError(null);
    setError('');
    try {
      await api.decide(
        requestedMission,
        taskId,
        decision,
        decision === 'approve' ? 'Reviewed in the War Room' : 'Rejected in the War Room',
      );
      dispatch({ type: 'snapshot', mission: await api.mission(requestedMission) });
    } catch (error) {
      if (currentMission.current === requestedMission) {
        setApprovalError(errorMessage(error));
        setError(errorMessage(error));
      }
      if (error instanceof ApiError && error.status === 409) {
        try {
          dispatch({ type: 'snapshot', mission: await api.mission(requestedMission) });
        } catch {
          /* Polling will refresh authoritative approval state. */
        }
      }
    } finally {
      decisionLock.current = false;
      setDeciding(null);
    }
  }
  const mission = state.mission;
  const pending = mission?.approvals.filter((approval) => approval.decision === 'requested') ?? [];
  const review =
    pending.find((approval) => approval.approval_id === reviewId) ??
    pending.find((approval) => !dismissed.includes(approval.approval_id));
  const completed = mission?.tasks.filter((task) => task.status === 'completed').length ?? 0;
  const activeQA = mission?.tasks.find(
    (task) => canonical(task.department) === 'qa_self_healer' && task.status === 'running',
  );
  const healing =
    state.healing[state.frame?.task_id ?? activeQA?.id ?? ''] ??
    Object.values(state.healing).sort((a, b) => b.sequence - a.sequence)[0] ??
    waitingHealing;

  return (
    <div className="app-shell" data-focus={focusMode ? 'true' : undefined}>
      <aside className="sidebar">
        <a href="/" className="brand">
          <div className="brand-icon">
            <Zap size={23} fill="currentColor" />
          </div>
          <span>
            openfoundry<span className="brand-dot">.</span>
            <small>AUTONOMOUS SOFTWARE STUDIO</small>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span className="tag">LOCAL</span>
        </div>
        <div className="nav-active">
          <Activity size={17} />
          War Room
          <span className="live-dot" />
        </div>
        <div className="sidebar-section-title">
          MISSIONS <span>{missions.length.toString().padStart(2, '0')}</span>
        </div>
        <nav className="mission-list" aria-label="Mission history">
          {missions.length ? (
            missions.map((item) => (
              <button
                key={item.id}
                className={item.id === missionId ? 'selected-mission' : ''}
                onClick={() => changeMission(item.id)}
              >
                <Circle size={8} fill="currentColor" className={`status-${item.status}`} />
                <span>{item.title}</span>
                <ChevronRight size={13} />
              </button>
            ))
          ) : (
            <p className="small subtle px-3">Your first mission starts here.</p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="small">
            <Shield size={15} />
            <span>
              Sandbox <strong>{catalog?.sandbox.execution_mode ?? 'checking'}</strong>
            </span>
          </div>
          <p>
            {catalog?.sandbox.execution_mode === 'guarded'
              ? 'Guarded host execution · 60s command limit'
              : (catalog?.sandbox.reason ?? 'Mission workspaces stay local.')}
          </p>
          <a href="https://github.com/MrMha/openfoundry" target="_blank" rel="noreferrer">
            Open source. In your control.
            <ArrowUpRight size={13} />
          </a>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div>
            <span className="subtle">Workspace</span>
            <ChevronRight size={14} />
            <strong>War Room</strong>
            <span className="tag">BETA</span>
          </div>
          <div className="backend-state">
            <button
              className="focus-toggle"
              onClick={() => setFocusMode((value) => !value)}
              aria-pressed={focusMode}
            >
              <Maximize2 size={13} />
              {focusMode ? 'Show mission controls' : 'Focus War Room'}
            </button>
            <span className={runtime ? 'live-dot' : 'offline-dot'} />
            <span role="status">{runtime ? 'Backend connected' : 'Backend disconnected'}</span>
            <span className="topbar-separator" />
            <span className="mono">v0.1</span>
          </div>
        </header>
        <div className="main-inner">
          <CommandBar
            runtime={runtime}
            blueprints={blueprints}
            mission={mission}
            busy={busy}
            token={token}
            authenticated={Boolean(runtime)}
            onToken={(next, remember) => {
              setToken(next);
              saveToken(remember ? next : '');
              setCredentialsVersion((value) => value + 1);
            }}
            onRun={(goal, configuration, blueprint) => {
              void launch(false, goal, configuration, blueprint);
            }}
            onDemo={() => {
              void launch(true);
            }}
          />
          {error && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={17} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError('')}>
                <X size={15} />
              </button>
            </div>
          )}
          <div className="mission-heading">
            <div>
              <span className="eyebrow">MISSION CONTROL</span>
              <h2>{mission?.title ?? 'Your team is standing by'}</h2>
              <p>
                {mission
                  ? `${completed} of ${mission.tasks.length} tasks complete · ${mission.id.slice(0, 8)}`
                  : 'Five departments. One shared goal. Full visibility.'}
              </p>
            </div>
            <div className="mission-indicators">
              {mission && (
                <span className={`status-pill status-${mission.status}`}>
                  {mission.status.replaceAll('_', ' ')}
                </span>
              )}
              <span className={`socket-state ${connection === 'live' ? 'verified' : 'subtle'}`}>
                <Radio size={14} />
                {missionId ? connection : 'Awaiting mission'}
              </span>
            </div>
          </div>
          {pending.map((approval) => (
            <section
              className="approval-gate"
              key={approval.approval_id}
              aria-label="Pending approval"
            >
              <div className="gate-icon">
                <Shield size={21} />
              </div>
              <div>
                <span className="eyebrow">HUMAN CHECKPOINT</span>
                <h3>
                  {mission?.tasks.find((task) => task.id === approval.task_id)?.title ??
                    'Review pending task'}
                </h3>
                <button
                  className="text-link"
                  onClick={() => {
                    setReviewId(approval.approval_id);
                    setApprovalError(null);
                  }}
                >
                  Review SPEC.md &amp; checkpoint <ArrowUpRight size={12} />
                </button>
              </div>
              <div className="approval-actions">
                <button
                  disabled={deciding !== null}
                  className="button button-amber"
                  onClick={() => {
                    void decide(approval.task_id, 'approve');
                  }}
                >
                  Approve &amp; Continue
                </button>
                <button
                  disabled={deciding !== null}
                  className="button button-secondary"
                  onClick={() => {
                    void decide(approval.task_id, 'reject');
                  }}
                >
                  Reject
                </button>
              </div>
            </section>
          ))}
          <div className="war-room-grid" key={missionId}>
            <section className="department-rail" aria-label="Registered departments">
              <div className="rail-heading">
                <span className="eyebrow">YOUR DEPARTMENTS</span>
                <span className="tag">
                  {catalog
                    ? DEPARTMENTS.filter((department) =>
                        catalog.plugins.some(
                          (plugin) => plugin.name === department.id && plugin.enabled,
                        ),
                      ).length
                    : '—'}{' '}
                  / 5 ONLINE
                </span>
              </div>
              {DEPARTMENTS.map((department) => (
                <DepartmentCard
                  key={department.id}
                  department={department.id}
                  plugin={catalog?.plugins.find((plugin) => plugin.name === department.id)}
                  catalogLoaded={Boolean(catalog)}
                  tasks={mission?.tasks ?? []}
                  thoughts={state.thoughts}
                  sandbox={catalog?.sandbox ?? null}
                  onSelectTask={setSelected}
                />
              ))}
            </section>
            <div className="work-column">
              <WarRoomGraph
                tasks={mission?.tasks ?? []}
                selected={selected}
                onSelect={setSelected}
              />
              <CodeDiffInspector
                api={api}
                missionId={mission?.id ?? null}
                artifacts={mission?.artifacts ?? []}
                diffs={state.diffs}
              />
            </div>
            <div className="observation-column">
              <LiveBrowserViewer api={api} frame={state.frame} healing={healing} />
              <section className="panel activity-panel" aria-label="Agent activity stream">
                <div className="panel-heading">
                  <div>
                    <Terminal size={17} />
                    <h2>Live evidence</h2>
                  </div>
                  <span className="tag">AGENT THOUGHTS</span>
                </div>
                <div className="evidence-body">
                  {!state.thoughts.length ? (
                    <div className="empty-state">
                      <Terminal size={25} />
                      <h3>The conversation starts with a mission.</h3>
                      <p>Decisions and repair progress appear here.</p>
                    </div>
                  ) : (
                    <ol
                      className="thought-log"
                      role="log"
                      aria-live="polite"
                      aria-label="Agent activity"
                    >
                      {state.thoughts.map((event) => (
                        <li key={event.event_id}>
                          <time>
                            {event.timestamp
                              ? new Date(event.timestamp).toLocaleTimeString([], { hour12: false })
                              : '—'}
                          </time>
                          <span className={`log-dot log-${event.level}`} />
                          <div>
                            <strong>{departmentName(event.department)}</strong>
                            <p>{event.thought}</p>
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              </section>
            </div>
          </div>
          <footer className="main-footer">
            <span>
              <Zap size={13} />
              Built in the open. Run on your terms.
            </span>
            <span>Artifacts stay in your mission workspace.</span>
          </footer>
        </div>
      </main>
      {review && (
        <ApprovalModal
          key={review.approval_id}
          api={api}
          approval={review}
          task={mission?.tasks.find((task) => task.id === review.task_id)}
          error={approvalError}
          onDecision={decide}
          onClose={() => {
            setDismissed((current) => [...current, review.approval_id]);
            setReviewId(null);
          }}
        />
      )}
    </div>
  );
}
