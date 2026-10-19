import { describe, expect, it } from 'vitest';
import saas from '../../../blueprints/saas_mvp_builder.json';
import website from '../../../blueprints/website_self_healer.json';
import career from '../../../blueprints/career_opportunity_hunter.json';
import { blueprintDefaults, renderBlueprint, renderBlueprintText } from './blueprints';
import type { Blueprint } from './types';
describe('shared backend blueprint templates', () => {
  for (const blueprint of [saas, website, career] as Blueprint[])
    it(`renders ${blueprint.id} defaults and all task parameters without unresolved tokens`, () => {
      const values = blueprintDefaults(blueprint);
      expect(renderBlueprint(blueprint, values)).not.toContain('$');
      for (const task of blueprint.tasks)
        expect(renderBlueprintText(task.description, values)).not.toContain('$');
      expect(blueprint.acceptance_criteria.length).toBeGreaterThan(3);
    });
  it('updates supplied parameters as plain text without interpreting their contents', () => {
    const template = saas as Blueprint;
    const parameters = {
      ...blueprintDefaults(template),
      idea: '<script>not code</script> $audience',
    };
    expect(renderBlueprint(template, parameters)).toContain('<script>not code</script> $audience');
  });
});
