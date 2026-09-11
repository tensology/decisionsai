// Independent sources retain their last successful value when another source fails.
export function createCatalogLoader(api) {
    const values = new Map();
    let pending = null;
    const sources = {
        projects: ['/projects', []],
        boards: ['/tickets/boards', []],
        externalBoards: ['/tickets/external-boards', {}],
        chatsData: ['/chats?include_archived=true&surface=development', { chats: [] }],
        workflows: ['/workflows?limit=200', []],
        runs: ['/workflows/active-runs?limit=100', []],
        inboxData: ['/workflows/intake/inbox?limit=100', { items: [] }],
        automationData: ['/automations', { automations: [] }],
        skillsData: ['/workflows/skills?limit=500', { skills: [] }]
    };
    async function performLoad() {
        const entries = Object.entries(sources);
        const results = await Promise.allSettled(entries.map(([, [path]]) => api(path)));
        const errors = [];
        const data = {};
        entries.forEach(([key, [, fallback]], index) => {
            const result = results[index];
            if (result.status === 'fulfilled') values.set(key, result.value);
            else errors.push(key);
            data[key] = values.has(key) ? values.get(key) : fallback;
        });
        return { ...data, errors };
    }
    return {
        load() {
            if (!pending)
                pending = performLoad().finally(() => {
                    pending = null;
                });
            return pending;
        }
    };
}
