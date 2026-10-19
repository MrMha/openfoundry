import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CodeDiffInspector, splitUnifiedDiff } from './CodeDiffInspector';
import { diff, missionId } from '../test/fixtures';
import type { ApiClient } from '../services/api';
import type { Artifact } from '../services/types';
const artifact: Artifact = {
  id: 'artifact-1',
  file_path: 'app.py',
  artifact_type: 'source',
  content_preview: 'stale preview',
};
function api() {
  return {
    file: vi.fn<ApiClient['file']>().mockResolvedValue({
      path: 'app.py',
      encoding: 'utf-8',
      content: 'def add(a,b): return a + b',
      size_bytes: 27,
    }),
  };
}
describe('live Git diff and workspace inspection', () => {
  it('pairs additions/deletions and preserves real hunk line numbers across multiple hunks', () => {
    const rows = splitUnifiedDiff(
      '--- a/app.py\n+++ b/app.py\n@@ -7,2 +7,3 @@\n-a\n-b\n+c\n+d\n+e\n@@ -30 +31 @@\n stable',
    );
    const code = rows.filter((row) => row.kind === 'code');
    expect(code[0]).toMatchObject({ left: { text: 'a', line: 7 }, right: { text: 'c', line: 7 } });
    expect(code[2]).toMatchObject({ left: null, right: { text: 'e', line: 9 } });
    expect(code[3]).toMatchObject({
      left: { text: 'stable', line: 30 },
      right: { text: 'stable', line: 31 },
    });
  });
  it('renders both diff formats, follows new check results, and safely inspects the actual workspace file', async () => {
    const client = api();
    const { rerender } = render(
      <CodeDiffInspector
        api={client}
        missionId={missionId}
        artifacts={[artifact]}
        diffs={[diff]}
      />,
    );
    const split = screen.getByRole('table', { name: 'Side-by-side code diff' });
    expect(within(split).getByText('return a - b')).toBeInTheDocument();
    expect(within(split).getByText('return a + b')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Unified' }));
    expect(screen.getByLabelText('Unified code diff')).toHaveTextContent('- return a - b');
    rerender(
      <CodeDiffInspector
        api={client}
        missionId={missionId}
        artifacts={[artifact]}
        diffs={[
          diff,
          {
            ...diff,
            event_id: 'verified-diff',
            sequence: 4,
            test_result: { ...diff.test_result, status: 'passed', return_code: 0 },
          },
        ]}
      />,
    );
    expect(screen.getByText('app.py · passed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect workspace file' }));
    await screen.findByText('def add(a,b): return a + b');
    expect(client.file).toHaveBeenCalledWith(missionId, 'app.py', expect.any(AbortSignal));
    expect(screen.queryByText('stale preview')).not.toBeInTheDocument();
  });
  it('bounds previews and renders code-like HTML as text', () => {
    render(
      <CodeDiffInspector
        api={api()}
        missionId={missionId}
        artifacts={[]}
        diffs={[
          {
            ...diff,
            unified_diff:
              '@@ -1 +1 @@\n-<script>bad()</script>\n+<img src=x onerror=bad()>\n' +
              ' '.repeat(100001),
          },
        ]}
      />,
    );
    expect(screen.getByText('<img src=x onerror=bad()>')).toBeInTheDocument();
    expect(document.querySelector('script, img')).toBeNull();
    expect(screen.getByText(/Preview truncated/)).toBeInTheDocument();
  });
  it('shows file errors and retries the same contained API path', async () => {
    const client = api();
    client.file.mockRejectedValueOnce(new Error('File temporarily unavailable'));
    render(
      <CodeDiffInspector api={client} missionId={missionId} artifacts={[artifact]} diffs={[]} />,
    );
    fireEvent.click(screen.getByRole('tab', { name: /^files/ }));
    fireEvent.click(screen.getByRole('button', { name: 'app.py' }));
    await screen.findByText('File temporarily unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Retry file inspection' }));
    await waitFor(() => expect(screen.getByText('def add(a,b): return a + b')).toBeInTheDocument());
    expect(client.file).toHaveBeenCalledTimes(2);
  });
});
