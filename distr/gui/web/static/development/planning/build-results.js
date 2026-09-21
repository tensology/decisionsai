// Stored Build output belongs to its conversation turn, not the composer.
export function buildResultsHtml(result, escapeHtml) {
    if (!result || !Array.isArray(result.tasks) || !result.tasks.length) return '';
    const tasks = result.tasks.slice(0, 200).filter(task => Number.isSafeInteger(task.id) && task.id > 0);
    const ids = new Set(tasks.map(task => task.id)), visited = new Set();
    const roots = tasks.filter(task => !ids.has(task.parent_id));
    function row(task) {
        if (visited.has(task.id)) return '';
        visited.add(task.id);
        const dependencies = Array.isArray(task.depends_on) ? task.depends_on.filter(id => Number.isSafeInteger(id) && id > 0) : [];
        const children = tasks.filter(child => child.parent_id === task.id).map(row).join('');
        return `<li><span>#${task.id} ${escapeHtml(String(task.title || 'Task'))}</span>${dependencies.length ? ` <small>After ${dependencies.map(id => `#${id}`).join(', ')}</small>` : ''}${children ? `<ul>${children}</ul>` : ''}</li>`;
    }
    const tree = roots.map(row).join('') + tasks.filter(task => !visited.has(task.id)).map(row).join('');
    return `<ul class="plan-build-results" aria-label="Generated tasks">${tree}</ul>`;
}
