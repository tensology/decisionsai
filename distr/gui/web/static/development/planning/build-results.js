// Stored Build output belongs to its conversation turn, not the composer.
export function buildResultsHtml(result, escapeHtml, { boardKey = '' } = {}) {
    if (!result || !Array.isArray(result.tasks) || !result.tasks.length) return '';
    const tasks = result.tasks.slice(0, 200).filter(task => Number.isSafeInteger(task.id) && task.id > 0);
    const ids = new Set(tasks.map(task => task.id)), visited = new Set();
    const roots = tasks.filter(task => !ids.has(task.parent_id));
    const boardId = /^decisions:([1-9]\d*)$/.exec(String(boardKey))?.[1];
    const ticket = (id, label, wrap = true) => boardId
        ? `<a class="plan-built-ticket" data-plan-ticket="${id}" href="/development/boards/decisions/${boardId}/kanban/?ticket_id=${id}">${escapeHtml(label)}</a>`
        : wrap ? `<span>${escapeHtml(label)}</span>` : escapeHtml(label);
    function row(task) {
        if (visited.has(task.id)) return '';
        visited.add(task.id);
        const dependencies = Array.isArray(task.depends_on) ? task.depends_on.filter(id => Number.isSafeInteger(id) && id > 0) : [];
        const children = tasks.filter(child => child.parent_id === task.id).map(row).join('');
        return `<li>${ticket(task.id, `#${task.id} ${String(task.title || 'Task')}`)}${dependencies.length ? ` <small>After ${dependencies.map(id => ticket(id, `#${id}`, false)).join(', ')}</small>` : ''}${children ? `<ul>${children}</ul>` : ''}</li>`;
    }
    const tree = roots.map(row).join('') + tasks.filter(task => !visited.has(task.id)).map(row).join('');
    return `<ul class="plan-build-results" aria-label="Generated tasks">${tree}</ul>`;
}
