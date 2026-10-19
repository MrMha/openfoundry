import { Check, FlaskConical, LoaderCircle, Wrench, Bug } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ApiClient } from '../services/api';
import { waitingHealing, type HealingProgress } from '../services/healingState';
import { loadFrameReport } from '../services/qaEvidence';
import type { BrowserFrameEvent, ProbeReport } from '../services/types';

interface Props {
  api: Pick<ApiClient, 'file'>;
  frame: BrowserFrameEvent | null;
  healing?: HealingProgress;
}
export function LiveBrowserViewer({ api, frame, healing = waitingHealing }: Props) {
  const [evidence, setEvidence] = useState<{
    frameId: string;
    report: ProbeReport | null;
    error: string | null;
  } | null>(null);
  const [brokenImage, setBrokenImage] = useState<string | null>(null);
  const frameId = frame
    ? (frame.event_id ?? `${frame.task_id}-${frame.frame_number}-${frame.timestamp}`)
    : '';
  useEffect(() => {
    if (!frame) return;
    const controller = new AbortController();
    void loadFrameReport(api, frame, controller.signal)
      .then((report) => {
        if (!controller.signal.aborted) setEvidence({ frameId, report, error: null });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setEvidence({
            frameId,
            report: null,
            error: 'Endpoint report unavailable for this frame',
          });
      });
    return () => controller.abort();
  }, [api, frame, frameId]);
  const report = evidence?.frameId === frameId ? evidence.report : null;
  const candidate = frame ? report?.results[(frame.frame_number ?? 0) % 8] : undefined;
  const probe = candidate?.url === frame?.current_url ? candidate : undefined;
  const errors = [...new Set([...(frame?.console_errors ?? []), ...(probe?.console_errors ?? [])])];
  const verified = healing.phase === 'verified';
  return (
    <section className="panel browser-panel" aria-label="Live browser and endpoint checks">
      <div className="panel-heading">
        <div>
          <FlaskConical size={17} />
          <h2>QA observation</h2>
        </div>
        <span className="tag">{frame ? `FRAME ${frame.frame_number ?? 0}` : 'STANDBY'}</span>
      </div>
      <div className="healing-header">
        <span className="eyebrow">SELF-HEALING LOOP</span>
        <span
          className={`status-${verified ? 'passed' : healing.phase === 'stopped' ? 'failed' : 'pending'}`}
          role="status"
        >
          {verified
            ? 'Verification complete'
            : healing.phase === 'stopped'
              ? 'Verification stopped'
              : 'Verification pending'}
        </span>
      </div>
      <ol
        className="healing-steps"
        aria-label="Self-Healing Loop: Bug Detected → Patching → Verified"
      >
        <li data-state={healing.bugDetected ? 'observed' : 'waiting'}>
          <Bug size={15} />
          <span>Bug Detected</span>
        </li>
        <li aria-hidden="true" className="healing-arrow">
          →
        </li>
        <li
          data-state={
            healing.phase === 'patching' ? 'active' : healing.patchObserved ? 'observed' : 'waiting'
          }
        >
          {healing.phase === 'patching' ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <Wrench size={15} />
          )}
          <span>Patching</span>
        </li>
        <li aria-hidden="true" className="healing-arrow">
          →
        </li>
        <li data-state={verified ? 'verified' : 'waiting'}>
          <Check size={15} />
          <span>Verified</span>
        </li>
      </ol>
      <div className="browser-address">
        <span className="browser-dots">
          <i />
          <i />
          <i />
        </span>
        <span title={frame?.current_url}>{frame?.current_url ?? 'Waiting for verification'}</span>
      </div>
      <div className="browser-frame">
        {frame && brokenImage !== frameId ? (
          <img
            key={frameId}
            src={`data:${frame.screenshot_mime_type ?? 'image/png'};base64,${frame.screenshot_base64}`}
            alt={`${probe?.backend === 'http-asgi-dom' ? 'DOM diagnostic' : 'QA'} frame at ${frame.current_url}`}
            onError={() => setBrokenImage(frameId)}
          />
        ) : (
          <div className="empty-state">
            <FlaskConical size={28} />
            <h3>{frame ? 'Frame image unavailable' : 'Trust, then verify.'}</h3>
            <p>
              {frame
                ? 'Inspect the endpoint results and errors below.'
                : 'Browser frames and endpoint diagnostics stream here during verification.'}
            </p>
          </div>
        )}
      </div>
      <div className="probe-badges" aria-label="Observed endpoint result">
        <span className={probe && !probe.passed ? 'error-badge' : 'neutral-badge'}>
          {probe
            ? probe.status === null
              ? 'HTTP unavailable'
              : `HTTP ${probe.status}`
            : 'HTTP status unknown'}
        </span>
        <span className={errors.length ? 'error-badge' : 'neutral-badge'}>
          {errors.length} console error{errors.length === 1 ? '' : 's'}
        </span>
        {probe && (
          <span className={probe.passed ? 'success-badge' : 'error-badge'}>
            {probe.passed ? 'DOM / endpoint passed' : 'DOM / endpoint failed'}
          </span>
        )}
      </div>
      <p className="browser-note small subtle">
        {probe
          ? probe.backend === 'http-asgi-dom'
            ? 'DOM / endpoint diagnostic card · no full browser coverage claimed.'
            : `Captured by ${probe.backend}.`
          : 'QA frame · capture backend not yet known.'}
      </p>
      {evidence?.frameId === frameId && evidence.error && (
        <p className="browser-note small subtle">{evidence.error}</p>
      )}
      {report?.error && <p className="browser-note text-rose-400 small">{report.error}</p>}
      {probe && (
        <div className="probe-results">
          <strong>
            {probe.path} · {probe.duration_seconds.toFixed(2)}s
          </strong>
          {probe.failures.map((failure, index) => (
            <p key={index} className="text-rose-400">
              {failure}
            </p>
          ))}
          {probe.warnings.map((warning, index) => (
            <p key={index} className="text-amber-400">
              {warning}
            </p>
          ))}
        </div>
      )}
      {!!errors.length && (
        <ul className="console-errors" aria-label="Console errors">
          {errors.slice(0, 20).map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      )}
      <div className="qa-caption">
        <span className={healing.bugDetected ? 'text-rose-400' : 'subtle'}>
          {healing.bugDetected
            ? `${healing.failureRounds} failed verification round${healing.failureRounds === 1 ? '' : 's'} observed`
            : verified
              ? 'No defect observed in streamed checks'
              : 'Waiting for check results'}
        </span>
        <span>{healing.patchObserved ? 'Repair recorded' : 'No repair recorded'}</span>
      </div>
    </section>
  );
}
