import { ArrowDownToLine, Code2, FileText, GitBranch, LoaderCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../services/api';
import type { Artifact, CodeDiffEvent, FileContent } from '../services/types';

interface Cell {
  text: string;
  line: number | null;
  changed: boolean;
}
export interface DiffRow {
  kind: 'meta' | 'code';
  label?: string;
  left: Cell | null;
  right: Cell | null;
}
const MAX_PREVIEW = 100000;
const MAX_LINES = 2000;
export function splitUnifiedDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldLine = 0,
    newLine = 0;
  let removed: Cell[] = [],
    added: Cell[] = [];
  function flush() {
    for (let index = 0; index < Math.max(removed.length, added.length); index++)
      rows.push({ kind: 'code', left: removed[index] ?? null, right: added[index] ?? null });
    removed = [];
    added = [];
  }
  for (const text of diff.slice(0, MAX_PREVIEW).split('\n').slice(0, MAX_LINES)) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {
      flush();
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: 'meta', label: text, left: null, right: null });
    } else if (
      text.startsWith('--- ') ||
      text.startsWith('+++ ') ||
      text.startsWith('diff ') ||
      text.startsWith('index ') ||
      text.startsWith('\\') ||
      !/^[ +-]/.test(text)
    ) {
      flush();
      rows.push({ kind: 'meta', label: text, left: null, right: null });
    } else if (text.startsWith('-'))
      removed.push({ text: text.slice(1), line: oldLine++, changed: true });
    else if (text.startsWith('+'))
      added.push({ text: text.slice(1), line: newLine++, changed: true });
    else {
      flush();
      rows.push({
        kind: 'code',
        left: { text: text.slice(1), line: oldLine++, changed: false },
        right: { text: text.slice(1), line: newLine++, changed: false },
      });
    }
  }
  flush();
  return rows;
}
function download(content: Blob, name: string) {
  const url = URL.createObjectURL(content);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
interface Props {
  api: Pick<ApiClient, 'file'>;
  missionId: string | null;
  artifacts: Artifact[];
  diffs: CodeDiffEvent[];
}
export function CodeDiffInspector({ api, missionId, artifacts, diffs }: Props) {
  const [tab, setTab] = useState<'changes' | 'files'>('changes');
  const [mode, setMode] = useState<'unified' | 'split'>('split');
  const [revision, setRevision] = useState('latest');
  const [path, setPath] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [retry, setRetry] = useState(0);
  const [resource, setResource] = useState<{
    key: string;
    file?: FileContent;
    error?: string;
  } | null>(null);
  const key = `${missionId}/${path}`;
  useEffect(() => {
    if (!missionId || !path) return;
    const controller = new AbortController();
    void api
      .file(missionId, path, controller.signal)
      .then((file) => {
        if (!controller.signal.aborted) setResource({ key, file });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setResource({ key, error: error instanceof Error ? error.message : 'File unavailable' });
      });
    return () => controller.abort();
  }, [api, missionId, path, key, retry]);
  const file = resource?.key === key ? resource.file : undefined;
  const fileError = resource?.key === key ? resource.error : undefined;
  const diff =
    diffs.find((event) => (event.event_id ?? String(event.sequence)) === revision) ?? diffs.at(-1);
  const rows = useMemo(() => splitUnifiedDiff(diff?.unified_diff ?? ''), [diff]);
  const files = [...new Map(artifacts.map((artifact) => [artifact.file_path, artifact])).values()]
    .sort((a, b) => a.file_path.localeCompare(b.file_path))
    .filter((artifact) => artifact.file_path.toLowerCase().includes(query.toLowerCase()));
  const truncated = Boolean(
    diff &&
    (diff.unified_diff.length > MAX_PREVIEW || diff.unified_diff.split('\n').length > MAX_LINES),
  );
  return (
    <section className="panel code-inspector" aria-label="Code diff inspector">
      <div className="panel-heading">
        <div>
          <Code2 size={17} />
          <h2>Code &amp; artifacts</h2>
        </div>
        <div className="tabs" role="tablist" aria-label="Code and artifacts">
          <button role="tab" aria-selected={tab === 'changes'} onClick={() => setTab('changes')}>
            changes
          </button>
          <button role="tab" aria-selected={tab === 'files'} onClick={() => setTab('files')}>
            files <span>{artifacts.length}</span>
          </button>
        </div>
      </div>
      {tab === 'changes' ? (
        <div role="tabpanel" aria-label="changes">
          {diff ? (
            <>
              <div className="diff-toolbar">
                <label className="sr-only" htmlFor="diff-revision">
                  Diff revision
                </label>
                <select
                  id="diff-revision"
                  value={revision}
                  onChange={(event) => setRevision(event.target.value)}
                >
                  <option value="latest">Follow latest change</option>
                  {diffs.map((event, index) => (
                    <option
                      key={event.event_id ?? index}
                      value={event.event_id ?? String(event.sequence)}
                    >
                      {event.file_path} · {event.test_result.status} · #{event.sequence}
                    </option>
                  ))}
                </select>
                <div className="diff-mode" role="group" aria-label="Diff presentation">
                  <button aria-pressed={mode === 'unified'} onClick={() => setMode('unified')}>
                    Unified
                  </button>
                  <button aria-pressed={mode === 'split'} onClick={() => setMode('split')}>
                    Side by side
                  </button>
                </div>
              </div>
              <div className="diff-revisions" aria-label="Recent check updates">
                {diffs.map((event, index) => (
                  <button
                    key={event.event_id ?? index}
                    className={`status-${event.test_result.status}`}
                    onClick={() => setRevision(event.event_id ?? String(event.sequence))}
                  >
                    {event.file_path} · {event.test_result.status.replaceAll('_', ' ')}
                  </button>
                ))}
              </div>
              <div className="diff-file-heading">
                <strong>{diff.file_path}</strong>
                <span className={`status-${diff.test_result.status}`}>
                  {diff.test_result.status.replaceAll('_', ' ')}
                </span>
                <button
                  className="text-link"
                  onClick={() => {
                    setPath(diff.file_path);
                    setTab('files');
                  }}
                >
                  Inspect workspace file
                </button>
              </div>
              {mode === 'unified' ? (
                <pre className="diff-code unified-diff" aria-label="Unified code diff">
                  {diff.unified_diff
                    .slice(0, MAX_PREVIEW)
                    .split('\n')
                    .slice(0, MAX_LINES)
                    .map((line, index) => (
                      <span
                        key={index}
                        className={
                          line.startsWith('+') && !line.startsWith('+++')
                            ? 'diff-add'
                            : line.startsWith('-') && !line.startsWith('---')
                              ? 'diff-remove'
                              : ''
                        }
                      >
                        {line}
                        {'\n'}
                      </span>
                    ))}
                </pre>
              ) : (
                <div className="split-diff" role="table" aria-label="Side-by-side code diff">
                  <div className="split-heading" role="row">
                    <span role="columnheader">Before</span>
                    <span role="columnheader">After</span>
                  </div>
                  {rows.map((row, index) =>
                    row.kind === 'meta' ? (
                      <div key={index} className="diff-meta" role="row">
                        <span role="cell">{row.label}</span>
                      </div>
                    ) : (
                      <div key={index} className="split-row" role="row">
                        {(['left', 'right'] as const).map((side) => (
                          <div
                            key={side}
                            role="cell"
                            className={
                              row[side]?.changed
                                ? side === 'left'
                                  ? 'diff-remove'
                                  : 'diff-add'
                                : ''
                            }
                          >
                            <span className="line-number" aria-hidden="true">
                              {row[side]?.line ?? ''}
                            </span>
                            <code>{row[side]?.text ?? ''}</code>
                          </div>
                        ))}
                      </div>
                    ),
                  )}
                </div>
              )}
              {truncated && (
                <p className="preview-notice">
                  Preview truncated to {MAX_PREVIEW.toLocaleString()} characters / {MAX_LINES}{' '}
                  lines. Download the complete diff for inspection.
                </p>
              )}
              <div className="diff-test-summary">
                <span>
                  {diff.test_result.command?.join(' ') || 'Verification result'} ·{' '}
                  {diff.test_result.duration_seconds?.toFixed(2) ?? '0'}s · exit{' '}
                  {diff.test_result.return_code ?? '—'}
                </span>
                <button
                  onClick={() =>
                    download(
                      new Blob([diff.unified_diff], { type: 'text/plain;charset=utf-8' }),
                      diff.file_path.split('/').pop() + '.diff',
                    )
                  }
                >
                  <ArrowDownToLine size={12} />
                  Download diff
                </button>
              </div>
              {diff.test_result.output_summary && (
                <details className="test-output">
                  <summary>Test output</summary>
                  <pre>{diff.test_result.output_summary}</pre>
                </details>
              )}
            </>
          ) : (
            <div className="empty-state">
              <GitBranch size={26} />
              <h3>Every change leaves a trail.</h3>
              <p>Engineering and QA stream unified Git diffs with measured test results.</p>
            </div>
          )}
        </div>
      ) : (
        <div role="tabpanel" aria-label="files">
          <div className="file-search">
            <label htmlFor="artifact-search" className="sr-only">
              Filter workspace artifacts
            </label>
            <input
              id="artifact-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter workspace artifacts…"
            />
          </div>
          <div className="file-view">
            <div className="file-list">
              {files.length ? (
                files.map((artifact) => (
                  <button
                    key={artifact.file_path}
                    className={path === artifact.file_path ? 'selected-file' : ''}
                    onClick={() => setPath(artifact.file_path)}
                  >
                    <FileText size={14} />
                    <span>{artifact.file_path}</span>
                  </button>
                ))
              ) : (
                <p className="small subtle">
                  {artifacts.length
                    ? 'No matching artifacts.'
                    : 'Artifacts appear as tasks finish.'}
                </p>
              )}
            </div>
            <div className="file-preview">
              {!path ? (
                <p className="small subtle">Select an artifact to inspect its contents.</p>
              ) : fileError ? (
                <div role="alert">
                  <p>{fileError}</p>
                  <button className="text-link" onClick={() => setRetry((value) => value + 1)}>
                    Retry file inspection
                  </button>
                </div>
              ) : !file ? (
                <LoaderCircle size={20} className="spin" aria-label="Loading file" />
              ) : (
                <>
                  <strong>{file.path}</strong>
                  {file.encoding === 'utf-8' ? (
                    <>
                      <pre>{file.content.slice(0, MAX_PREVIEW)}</pre>
                      {file.content.length > MAX_PREVIEW && (
                        <p className="preview-notice">
                          File preview truncated. Download for complete content.
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="small subtle">
                      Binary artifact · {file.size_bytes.toLocaleString()} bytes
                    </p>
                  )}
                  <button
                    className="text-link"
                    onClick={() =>
                      download(
                        file.encoding === 'base64'
                          ? new Blob(
                              [Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0))],
                              { type: 'application/octet-stream' },
                            )
                          : new Blob([file.content], { type: 'text/plain;charset=utf-8' }),
                        file.path.split('/').pop() ?? 'artifact',
                      )
                    }
                  >
                    <ArrowDownToLine size={13} />
                    Download file
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
