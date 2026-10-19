import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { layoutTasks, WarRoomGraph } from './WarRoomGraph';
import type { TaskNode } from '../services/types';
const task = (
  id: string,
  dependencies: string[],
  status: TaskNode['status'] = 'pending',
): TaskNode => ({
  id,
  mission_id: 'mission',
  department: id === 'plan' ? 'chief_of_staff' : 'engineer',
  title: id,
  description: 'A concrete task',
  dependencies,
  status,
  output_summary: null,
  requires_approval: false,
});
describe('task DAG', () => {
  it('positions tasks from real dependency relationships, including parallel branches', () => {
    const positions = layoutTasks([
      task('merge', ['a', 'b']),
      task('a', ['plan']),
      task('plan', []),
      task('b', ['plan']),
    ]);
    const byId = Object.fromEntries(positions.map((node) => [node.task.id, node]));
    expect(byId.a.x).toBe(byId.b.x);
    expect(byId.a.y).not.toBe(byId.b.y);
    expect(byId.merge.x).toBeGreaterThan(byId.a.x);
    expect(byId.plan.x).toBeLessThan(byId.a.x);
  });
  it('renders dependency edges and allows keyboard task inspection and graph zoom/reset', () => {
    const onSelect = vi.fn();
    render(
      <WarRoomGraph
        tasks={[task('plan', [], 'completed'), task('build', ['plan'], 'running')]}
        selected={null}
        onSelect={onSelect}
      />,
    );
    expect(screen.getAllByTestId('dependency-edge')).toHaveLength(1);
    expect(screen.getByTestId('dependency-edge')).toHaveClass('active-edge');
    fireEvent.keyDown(screen.getByRole('button', { name: 'build: running' }), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('build');
    fireEvent.click(screen.getByLabelText('Zoom in'));
    expect(screen.getByText('115%')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Reset graph view'));
    expect(screen.getByText('100%')).toBeInTheDocument();
  });
});
