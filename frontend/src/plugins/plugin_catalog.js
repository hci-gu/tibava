function cloneParameter(parameter) {
  return {
    ...parameter,
    buttons: parameter.buttons ? [...parameter.buttons] : parameter.buttons,
    items: parameter.items ? [...parameter.items] : parameter.items,
  };
}

export function clonePluginCatalog(catalog) {
  return (catalog || []).map((group, groupIndex) => ({
    ...group,
    id: group.id || groupIndex + 1,
    children: (group.children || []).map((plugin, pluginIndex) => ({
      ...plugin,
      id: plugin.id || (group.id || groupIndex + 1) * 100 + pluginIndex + 1,
      parameters: (plugin.parameters || []).map(cloneParameter),
      optional_parameters: (plugin.optional_parameters || []).map(cloneParameter),
    })),
  }));
}

export function flattenPluginCatalog(catalog) {
  return clonePluginCatalog(catalog).flatMap((group) => group.children);
}
