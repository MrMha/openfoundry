import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import template from '../../../blueprints/saas_mvp_builder.json';
import type { Blueprint } from '../services/types';
import { CommandBar } from './CommandBar';
import type { Runtime } from '../services/types';
const runtime: Runtime = {
  auth_required: false,
  demo_available: true,
  max_mission_cost_usd: '2',
  providers: [{ provider: 'openai', model: 'openai/gpt-4o-mini', configured: true }],
};
function props() {
  return {
    runtime,
    blueprints: [template as Blueprint],
    mission: null,
    busy: false,
    token: '',
    authenticated: true,
    onToken: vi.fn(),
    onRun: vi.fn(),
    onDemo: vi.fn(),
  };
}
describe('mission controls', () => {
  it('runs key-free demo and sends selected blueprint with provider/model/budget configuration', () => {
    const callbacks = props();
    render(<CommandBar {...callbacks} />);
    fireEvent.click(screen.getByRole('button', { name: '⚡ Run Self-Healing Demo (No API Key)' }));
    expect(callbacks.onDemo).toHaveBeenCalledOnce();
    fireEvent.change(screen.getByLabelText('Blueprint'), { target: { value: 'saas_mvp_builder' } });
    expect((screen.getByLabelText('CEO mission prompt') as HTMLTextAreaElement).value).toContain(
      'notes app',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Local access' }));
    fireEvent.change(screen.getByLabelText('Provider for next mission'), {
      target: { value: 'openai' },
    });
    fireEvent.change(screen.getByLabelText('Next mission budget (USD)'), {
      target: { value: '1.25' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch mission' }));
    expect(callbacks.onRun).toHaveBeenCalledWith(
      expect.stringContaining('notes app'),
      {
        provider: 'openai',
        model: 'openai/gpt-4o-mini',
        budget_usd: '1.25',
      },
      {
        blueprint_id: template.id,
        blueprint_parameters: Object.fromEntries(
          template.parameters.map((p) => [p.key, p.default]),
        ),
      },
    );
  });
  it('supports token entry without requiring a provider API key or silently storing credentials', () => {
    const callbacks = props();
    render(<CommandBar {...callbacks} authenticated={false} />);
    expect(screen.getByRole('button', { name: /Run Self-Healing Demo/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Connect backend' }));
    fireEvent.change(screen.getByLabelText('Operator token'), { target: { value: 'operator' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(callbacks.onToken).toHaveBeenCalledWith('operator', false);
  });
});

it('keeps blueprint parameter edits, task previews, and mission goal synchronized', () => {
  const callbacks = props();
  render(<CommandBar {...callbacks} />);
  fireEvent.change(screen.getByLabelText('Blueprint'), { target: { value: template.id } });
  fireEvent.change(screen.getByLabelText('Product idea'), {
    target: { value: 'a lending library' },
  });
  expect((screen.getByLabelText('CEO mission prompt') as HTMLTextAreaElement).value).toContain(
    'a lending library',
  );
  expect(screen.getByText(/Research a lending library/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('CEO mission prompt'), {
    target: { value: 'My edited scope' },
  });
  fireEvent.change(screen.getByLabelText('Target audience'), { target: { value: 'librarians' } });
  expect(screen.getByLabelText('CEO mission prompt')).toHaveValue('My edited scope');
});
