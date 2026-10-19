import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApprovalModal } from './ApprovalModal';
import { approval, task } from '../test/fixtures';
import type { ApiClient } from '../services/api';
function props() {
  return {
    api: {
      file: vi.fn<ApiClient['file']>().mockResolvedValue({
        path: 'SPEC.md',
        encoding: 'utf-8' as const,
        content: '# Calculator specification',
        size_bytes: 26,
      }),
    },
    approval,
    task,
    onDecision: vi
      .fn<(id: string, decision: 'approve' | 'reject') => Promise<void>>()
      .mockResolvedValue(),
    onClose: vi.fn(),
    error: null,
  };
}
describe('human checkpoint review', () => {
  it('loads the real mission specification, guards double submission, and wires Approve & Continue', async () => {
    const callbacks = props();
    let finish: (() => void) | undefined;
    callbacks.onDecision.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    render(<ApprovalModal {...callbacks} />);
    expect(screen.getByRole('dialog', { name: task.title })).toHaveAttribute('open');
    await screen.findByText('# Calculator specification');
    expect(callbacks.api.file).toHaveBeenCalledWith(
      approval.mission_id,
      'SPEC.md',
      expect.any(AbortSignal),
    );
    const approve = screen.getByRole('button', { name: 'Approve & Continue' });
    fireEvent.click(approve);
    fireEvent.click(approve);
    expect(callbacks.onDecision).toHaveBeenCalledExactlyOnceWith(task.id, 'approve');
    expect(approve).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled();
    const escape = new Event('cancel', { cancelable: true });
    fireEvent(screen.getByRole('dialog'), escape);
    expect(escape.defaultPrevented).toBe(true);
    finish?.();
    await waitFor(() => expect(approve).not.toBeDisabled());
  });
  it('Reject submits the matching task and close leaves approval pending', async () => {
    const callbacks = props();
    const { unmount } = render(<ApprovalModal {...callbacks} />);
    await screen.findByText('# Calculator specification');
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(callbacks.onDecision).toHaveBeenCalledWith(task.id, 'reject'));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Close approval review' })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close approval review' }));
    expect(callbacks.onClose).toHaveBeenCalledOnce();
    unmount();
  });
  it('shows missing SPEC and backend approval errors without claiming success', async () => {
    const callbacks = props();
    callbacks.api.file.mockRejectedValue(new Error('Specification unavailable'));
    render(<ApprovalModal {...callbacks} error="Approval already has a different decision" />);
    await screen.findByText('Specification unavailable');
    expect(screen.getByRole('alert')).toHaveTextContent('different decision');
    expect(callbacks.onDecision).not.toHaveBeenCalled();
  });
});
