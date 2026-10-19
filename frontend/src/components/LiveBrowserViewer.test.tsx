import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LiveBrowserViewer } from './LiveBrowserViewer';
import { frame, report, taskId } from '../test/fixtures';
import { waitingHealing } from '../services/healingState';
import { parseProbeReport } from '../services/qaEvidence';
import type { ApiClient } from '../services/api';
const file = () =>
  vi.fn<ApiClient['file']>().mockResolvedValue({
    path: 'probes.json',
    encoding: 'utf-8',
    content: JSON.stringify(report),
    size_bytes: 400,
  });
describe('live browser and structured endpoint evidence', () => {
  it('renders frames, measured HTTP/DOM failures, console badges and the repair progress indicator', async () => {
    const api = { file: file() };
    render(
      <LiveBrowserViewer
        api={api}
        frame={frame}
        healing={{
          ...waitingHealing,
          phase: 'patching',
          bugDetected: true,
          patchObserved: true,
          failureRounds: 1,
        }}
      />,
    );
    await screen.findByText('HTTP 503');
    expect(api.file).toHaveBeenCalledWith(
      frame.mission_id,
      `qa/${taskId}/round-0/probes.json`,
      expect.any(AbortSignal),
    );
    expect(screen.getByText('DOM / endpoint failed')).toBeInTheDocument();
    expect(screen.getByText('1 console error')).toBeInTheDocument();
    expect(screen.getByAltText(/DOM diagnostic frame/)).toHaveAttribute(
      'src',
      `data:image/png;base64,${frame.screenshot_base64}`,
    );
    expect(
      screen.getByLabelText('Self-Healing Loop: Bug Detected → Patching → Verified'),
    ).toHaveTextContent('Bug Detected');
    expect(screen.getByText('Patching').closest('li')).toHaveAttribute('data-state', 'active');
    expect(screen.getByText('Missing Ready text')).toBeInTheDocument();
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByText('Frame image unavailable')).toBeInTheDocument();
  });
  it('shows unknown HTTP status when no valid report exists and never converts missing evidence into a pass', async () => {
    const api = { file: vi.fn<ApiClient['file']>().mockRejectedValue(new Error('Not available')) };
    render(<LiveBrowserViewer api={api} frame={{ ...frame, console_errors: [] }} />);
    await screen.findByText('Endpoint report unavailable for this frame');
    expect(screen.getByText('HTTP status unknown')).toBeInTheDocument();
    expect(screen.queryByText('DOM / endpoint passed')).not.toBeInTheDocument();
    expect(screen.getByText('Verification pending')).toBeInTheDocument();
    expect(() => parseProbeReport('{"results":[{"status":"200"}],"error":null}')).toThrow(
      'backend probe schema',
    );
  });
  it('cancels stale evidence requests and presents the latest passing diagnostic result', async () => {
    let resolveOld: ((value: Awaited<ReturnType<ApiClient['file']>>) => void) | undefined;
    const fetch = file().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const api = { file: fetch };
    const { rerender } = render(<LiveBrowserViewer api={api} frame={frame} />);
    const passed = {
      ...report,
      results: [
        { ...report.results[0], status: 200, passed: true, failures: [], console_errors: [] },
      ],
    };
    fetch.mockResolvedValue({
      path: 'probes.json',
      encoding: 'utf-8',
      content: JSON.stringify(passed),
      size_bytes: 200,
    });
    rerender(
      <LiveBrowserViewer
        api={api}
        frame={{ ...frame, event_id: 'new-frame', frame_number: 8, console_errors: [] }}
        healing={{ ...waitingHealing, phase: 'verified' }}
      />,
    );
    await screen.findByText('HTTP 200');
    resolveOld?.({
      path: 'probes.json',
      encoding: 'utf-8',
      content: JSON.stringify(report),
      size_bytes: 100,
    });
    await waitFor(() => expect(screen.getByText('DOM / endpoint passed')).toBeInTheDocument());
    expect(fetch.mock.calls[0][2]?.aborted).toBe(true);
    expect(screen.queryByText('HTTP 503')).not.toBeInTheDocument();
  });
});
