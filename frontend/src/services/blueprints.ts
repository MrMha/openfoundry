import type { Blueprint } from './types';
export function blueprintDefaults(blueprint: Blueprint): Record<string, string> {
  return Object.fromEntries(
    blueprint.parameters.map((parameter) => [parameter.key, parameter.default]),
  );
}
export function renderBlueprint(blueprint: Blueprint, parameters: Record<string, string>): string {
  return renderBlueprintText(blueprint.goal_template, parameters);
}
export function renderBlueprintText(text: string, parameters: Record<string, string>): string {
  return text.replace(
    /\$\$|\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)/g,
    (match, braced: string | undefined, plain: string | undefined) =>
      match === '$$' ? '$' : (parameters[braced ?? plain ?? ''] ?? ''),
  );
}
