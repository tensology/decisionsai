// Provider-neutral catalog helpers shared by threads, workflows, and automations.
export function providerLabel(providerId, providers) {
    const row = providers.find((provider) => String(provider.id || provider).toLowerCase() === String(providerId || '').toLowerCase());
    return String(row?.name || row?.id || providerId || 'Auto');
}

export function catalogModels(providerId, catalogs) {
    return catalogs[String(providerId || '').toLowerCase()] || [];
}
