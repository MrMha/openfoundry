import {
  ArrowUpRight,
  Check,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
  SlidersHorizontal,
  Zap,
} from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { blueprintDefaults, renderBlueprint, renderBlueprintText } from '../services/blueprints';
import type {
  Blueprint,
  BlueprintSelection,
  MissionConfiguration,
  MissionDetail,
  Provider,
  Runtime,
} from '../services/types';

interface Props {
  runtime: Runtime | null;
  blueprints: Blueprint[];
  mission: MissionDetail | null;
  busy: boolean;
  token: string;
  authenticated: boolean;
  onToken: (token: string, remember: boolean) => void;
  onRun: (goal: string, configuration: MissionConfiguration, blueprint: BlueprintSelection) => void;
  onDemo: () => void;
}
export function CommandBar({
  runtime,
  blueprints,
  mission,
  busy,
  token,
  authenticated,
  onToken,
  onRun,
  onDemo,
}: Props) {
  const [goal, setGoal] = useState('');
  const [blueprint, setBlueprint] = useState('');
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [goalEdited, setGoalEdited] = useState(false);
  const selectedBlueprint = blueprints.find((item) => item.id === blueprint);
  const [provider, setProvider] = useState<Provider | ''>('');
  const [model, setModel] = useState('');
  const [budget, setBudget] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [tokenDraft, setTokenDraft] = useState(token);
  const [remember, setRemember] = useState(Boolean(token));
  useEffect(() => {
    if (authenticated) setShowSettings(false);
  }, [authenticated]);
  const ceiling = Number(runtime?.max_mission_cost_usd ?? 2);
  const nextBudget = budget || String(ceiling);
  const effectiveLimit = Number(mission?.execution.budget_usd ?? ceiling);
  const spent = Number(mission?.total_cost_usd ?? 0);
  const reserved = Number(mission?.reserved_cost_usd ?? 0);
  const configured = runtime?.providers.some((item) => item.configured) ?? false;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim() || busy) return;
    onRun(
      goal.trim(),
      {
        provider: provider || null,
        model: provider ? model || null : null,
        budget_usd: nextBudget,
      },
      selectedBlueprint
        ? { blueprint_id: selectedBlueprint.id, blueprint_parameters: parameters }
        : {},
    );
  }
  return (
    <section className="command-shell" aria-label="Mission command center">
      <div className="command-title">
        <div>
          <span className="eyebrow">YOUR NEXT BIG THING</span>
          <h1>
            Give your company a mission<span className="text-emerald-400">.</span>
          </h1>
        </div>
        <button
          className={`icon-label ${authenticated ? 'verified' : ''}`}
          onClick={() => setShowSettings(!showSettings)}
          aria-expanded={showSettings}
        >
          <KeyRound size={15} />
          {authenticated
            ? runtime?.auth_required
              ? 'Operator authenticated'
              : 'Local access'
            : 'Connect backend'}
          <SlidersHorizontal size={14} />
        </button>
      </div>
      <form onSubmit={submit}>
        <label className="sr-only" htmlFor="mission-goal">
          CEO mission prompt
        </label>
        <textarea
          id="mission-goal"
          value={goal}
          onChange={(event) => {
            setGoal(event.target.value);
            setGoalEdited(true);
          }}
          maxLength={16384}
          rows={2}
          placeholder="Describe what you want to build. Your team takes it from here."
        />
        <div className="command-actions">
          <div className="command-selects">
            <label>
              <span className="sr-only">Blueprint</span>
              <select
                aria-label="Blueprint"
                value={blueprint}
                onChange={(event) => {
                  const value = event.target.value;
                  setBlueprint(value);
                  const template = blueprints.find((item) => item.id === value);
                  setGoalEdited(false);
                  if (template) {
                    const values = blueprintDefaults(template);
                    setParameters(values);
                    setGoal(renderBlueprint(template, values));
                    setBudget(
                      String(
                        Math.min(
                          ceiling,
                          Number(template.suggested_configuration.budget_usd ?? ceiling),
                        ),
                      ),
                    );
                    const suggested = template.suggested_configuration.provider;
                    if (
                      suggested &&
                      runtime?.providers.some(
                        (item) => item.provider === suggested && item.configured,
                      )
                    ) {
                      setProvider(suggested);
                      setModel(template.suggested_configuration.model ?? '');
                    }
                  }
                }}
              >
                <option value="">Choose a blueprint</option>
                {blueprints.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
            <span className="subtle small">
              {provider || 'Auto routing'} <span className="dot-divider">·</span> $
              {Number(nextBudget).toFixed(2)} ceiling
            </span>
          </div>
          <button
            type="submit"
            className="button button-primary"
            disabled={
              busy ||
              !goal.trim() ||
              !authenticated ||
              !configured ||
              Boolean(
                selectedBlueprint?.parameters.some(
                  (parameter) => parameter.required && !parameters[parameter.key]?.trim(),
                ),
              )
            }
          >
            {busy ? <LoaderCircle size={16} className="spin" /> : <ArrowUpRight size={17} />}Launch
            mission
          </button>
        </div>
      </form>
      {selectedBlueprint && (
        <div className="blueprint-configuration">
          <p>{selectedBlueprint.description}</p>
          <div className="blueprint-parameters">
            {selectedBlueprint.parameters.map((parameter) => (
              <label key={parameter.key}>
                {parameter.label}
                <input
                  aria-label={parameter.label}
                  type={parameter.kind === 'url' ? 'url' : 'text'}
                  value={parameters[parameter.key] ?? ''}
                  maxLength={4000}
                  required={parameter.required}
                  title={parameter.description}
                  onChange={(event) => {
                    const next = { ...parameters, [parameter.key]: event.target.value };
                    setParameters(next);
                    if (!goalEdited) setGoal(renderBlueprint(selectedBlueprint, next));
                  }}
                />
              </label>
            ))}
          </div>
          <details>
            <summary>Template tasks · Chief of Staff adapts this plan</summary>
            <ol>
              {selectedBlueprint.tasks.map((task) => (
                <li key={task.key}>
                  <strong>{task.title}</strong> · {task.department}{' '}
                  {task.requires_approval ? '· approval required' : ''}
                  <p>{renderBlueprintText(task.description, parameters)}</p>
                </li>
              ))}
            </ol>
          </details>
        </div>
      )}
      {showSettings && (
        <div className="configuration-grid">
          <div className="auth-settings">
            <label htmlFor="operator-token">Operator token</label>
            <div className="input-row">
              <input
                id="operator-token"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={tokenDraft}
                onChange={(event) => setTokenDraft(event.target.value)}
                placeholder="Leave empty for local access"
              />
              <button
                className="button button-secondary"
                onClick={() => onToken(tokenDraft.trim(), remember)}
              >
                <Check size={15} />
                Connect
              </button>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
              />
              Remember on this browser
            </label>
          </div>
          <div>
            <label htmlFor="provider">Provider for next mission</label>
            <select
              id="provider"
              value={provider}
              onChange={(event) => {
                const value = event.target.value as Provider | '';
                setProvider(value);
                setModel(runtime?.providers.find((item) => item.provider === value)?.model ?? '');
              }}
            >
              <option value="">Auto · server fallback order</option>
              {runtime?.providers.map((item) => (
                <option key={item.provider} value={item.provider} disabled={!item.configured}>
                  {item.provider}
                  {item.configured ? '' : ' · key not configured'}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="model">Model</label>
            <input
              id="model"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              disabled={!provider}
              placeholder="Server default"
            />
          </div>
          <div>
            <label htmlFor="budget">Next mission budget (USD)</label>
            <input
              id="budget"
              type="number"
              min="0.000000001"
              max={ceiling}
              step="any"
              value={nextBudget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </div>
          <p className="small subtle config-note">
            Provider keys stay on the backend. The demo works without them. Your operator token
            grants access to all missions on this server.
          </p>
        </div>
      )}
      <div className="demo-strip">
        <div>
          <span className="mini-badge">
            <Zap size={12} />
            TRY IT LIVE
          </span>
          <span className="small">One seeded bug. One autonomous repair. Watch every step.</span>
        </div>
        <button className="button button-demo" onClick={onDemo} disabled={busy || !authenticated}>
          ⚡ Run Self-Healing Demo (No API Key)
        </button>
      </div>
      <div className="budget-strip">
        <div className="budget-caption">
          <ShieldCheck size={14} />
          <span>Mission budget</span>
          <strong>
            ${spent.toFixed(4)} <span className="subtle">/ ${effectiveLimit.toFixed(2)}</span>
          </strong>
        </div>
        <div
          className="budget-track"
          role="progressbar"
          aria-label="Mission cost and reservations"
          aria-valuemin={0}
          aria-valuemax={effectiveLimit}
          aria-valuenow={spent + reserved}
        >
          <span style={{ width: `${Math.min(100, (spent / effectiveLimit) * 100)}%` }} />
          <i style={{ width: `${Math.min(100, (reserved / effectiveLimit) * 100)}%` }} />
        </div>
        <span className="small subtle">
          {mission?.execution.mode === 'demo'
            ? 'Offline demo · $0 API cost'
            : reserved > 0
              ? `$${reserved.toFixed(4)} reserved`
              : 'Cost ceiling enforced'}
        </span>
      </div>
    </section>
  );
}
