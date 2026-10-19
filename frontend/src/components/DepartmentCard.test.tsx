import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DepartmentCard } from './DepartmentCard';
import { plugin, sandbox, task, thought } from '../test/fixtures';

describe('registered department status and measured sandbox', () => {
  it('shows current task, live thoughts, and measured capability and opens task inspection', () => {
    const onSelectTask = vi.fn();
    const props = {
      department: 'engineer',
      plugin,
      catalogLoaded: true,
      tasks: [task],
      thoughts: [thought],
      sandbox,
      onSelectTask,
    };
    const { rerender } = render(<DepartmentCard {...props} />);
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('guarded')).toBeInTheDocument();
    expect(screen.getByText('Measured capability')).toBeInTheDocument();
    expect(screen.getByText('Building calculator')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Build calculator/ }));
    expect(onSelectTask).toHaveBeenCalledWith(task.id);
    rerender(
      <DepartmentCard
        {...props}
        tasks={[{ ...task, status: 'completed' }]}
        thoughts={[
          thought,
          { ...thought, event_id: 'thought-2', thought: '<script>repair verified</script>' },
        ]}
      />,
    );
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('<script>repair verified</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
  });
  it('reports unknown/disabled plugins honestly and supports legacy task identifiers', () => {
    const { rerender } = render(
      <DepartmentCard
        department="engineer"
        catalogLoaded={false}
        tasks={[]}
        thoughts={[]}
        sandbox={null}
        onSelectTask={vi.fn()}
      />,
    );
    expect(screen.getByText('unknown')).toBeInTheDocument();
    rerender(
      <DepartmentCard
        department="engineer"
        catalogLoaded
        plugin={{ ...plugin, enabled: false }}
        tasks={[{ ...task, department: 'engineering' }]}
        thoughts={[]}
        sandbox={{ ...sandbox, capability_probe_performed: false, execution_mode: 'unavailable' }}
        onSelectTask={vi.fn()}
      />,
    );
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Build calculator/ })).toBeInTheDocument();
  });
});
