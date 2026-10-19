import { Check, LoaderCircle, Shield, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import type { ApiClient } from '../services/api';
import type { Approval, FileContent, TaskNode } from '../services/types';

interface Props {
  api: Pick<ApiClient, 'file'>;
  approval: Approval;
  task?: TaskNode;
  onDecision: (taskId: string, decision: 'approve' | 'reject') => Promise<void>;
  onClose: () => void;
  error: string | null;
}
export function ApprovalModal({ api, approval, task, onDecision, onClose, error }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [spec, setSpec] = useState<FileContent | null>(null);
  const [specError, setSpecError] = useState('');
  const title = useId();
  const description = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement as HTMLElement | null;
    element?.showModal();
    element?.querySelector<HTMLButtonElement>('.modal-close')?.focus();
    return () => {
      element?.close();
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void api
      .file(approval.mission_id, 'SPEC.md', controller.signal)
      .then((file) => {
        if (!controller.signal.aborted) setSpec(file);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setSpecError(error instanceof Error ? error.message : 'Specification unavailable');
      });
    return () => controller.abort();
  }, [api, approval.mission_id]);
  async function decide(decision: 'approve' | 'reject') {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      await onDecision(approval.task_id, decision);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="approval-modal"
      aria-labelledby={title}
      aria-describedby={description}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return;
        const controls = [
          ...event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not([disabled]), summary, a[href], input:not([disabled]), [tabindex="0"]',
          ),
        ];
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onCancel={(event) => {
        if (pending.current) event.preventDefault();
        else onClose();
      }}
    >
      <div className="modal-heading">
        <div className="gate-icon">
          <Shield size={23} />
        </div>
        <div>
          <span className="eyebrow">HUMAN CHECKPOINT</span>
          <h2 id={title}>{task?.title ?? 'Review pending task'}</h2>
        </div>
        <button
          className="modal-close"
          autoFocus
          disabled={busy}
          onClick={onClose}
          aria-label="Close approval review"
        >
          <X size={18} />
        </button>
      </div>
      <p id={description} className="approval-description">
        {task?.description ?? 'Review the mission scope before allowing this task to execute.'}
      </p>
      <div className="approval-scope">
        <span>
          Department <strong>{task?.department ?? 'Unknown'}</strong>
        </span>
        <span>
          Task <code>{approval.task_id.slice(0, 8)}</code>
        </span>
      </div>
      <details open className="approval-spec">
        <summary>SPEC.md · mission specification</summary>
        {spec ? (
          spec.encoding === 'utf-8' ? (
            <pre>{spec.content}</pre>
          ) : (
            <p>Specification is not a text file.</p>
          )
        ) : specError ? (
          <p className="text-rose-400">{specError}</p>
        ) : (
          <p className="subtle">Loading specification…</p>
        )}
      </details>
      <p className="small subtle">
        Approval permits this task to execute. Reject cancels remaining mission work. Closing this
        review leaves the checkpoint pending.
      </p>
      {error && (
        <p role="alert" className="modal-error">
          {error}
        </p>
      )}
      <div className="approval-actions">
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => {
            void decide('reject');
          }}
        >
          Reject
        </button>
        <button
          className="button button-amber"
          disabled={busy}
          onClick={() => {
            void decide('approve');
          }}
        >
          {busy ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}Approve &amp;
          Continue
        </button>
      </div>
    </dialog>
  );
}
