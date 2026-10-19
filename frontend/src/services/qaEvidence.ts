import Ajv2020 from 'ajv/dist/2020';
import schema from './probes.schema.json';
import type { ApiClient } from './api';
import type { BrowserFrameEvent, ProbeReport } from './types';

const validate = new Ajv2020({ strict: false }).compile(schema);
export function parseProbeReport(content: string): ProbeReport {
  const value: unknown = JSON.parse(content);
  if (!validate(value))
    throw new Error('Endpoint evidence does not match the backend probe schema');
  return value as ProbeReport;
}
export async function loadFrameReport(
  api: Pick<ApiClient, 'file'>,
  frame: BrowserFrameEvent,
  signal: AbortSignal,
): Promise<ProbeReport | null> {
  if (!frame.task_id || !['qa_self_healer', 'qa'].includes(frame.department ?? 'qa')) return null;
  // The bundled QA worker numbers frames as iteration * 8 + probe index and writes
  // probes.json before emitting each frame. Custom producers can omit this evidence.
  const round = Math.floor((frame.frame_number ?? 0) / 8);
  const file = await api.file(
    frame.mission_id,
    `qa/${frame.task_id}/round-${round}/probes.json`,
    signal,
  );
  if (file.encoding !== 'utf-8') throw new Error('Endpoint evidence must be JSON text');
  return parseProbeReport(file.content);
}
