// The embedded plan reuses the thread header breadcrumb (#development-task-header .task-breadcrumb):
// "<project> / plan / <section>". Extra segments are added as the same <i>/</i><strong> pair.

export function planCrumbs(projectName, ...rest) {
    return [String(projectName || 'Development'), 'Plan', ...rest.filter(Boolean).map(String)];
}

export function renderPlanCrumbs(doc, segments) {
    const project = doc.getElementById('header-project');
    const task = doc.getElementById('header-task');
    if (!project || !task) return;
    clearPlanCrumbs(doc);
    const [first, second, ...rest] = segments;
    project.textContent = first;
    task.textContent = second;
    // Only the last segment is emphasised; earlier ones render muted like the project.
    if (rest.length) task.dataset.planCrumbParent = '';
    let after = task;
    for (const text of rest) {
        const slash = doc.createElement('i');
        slash.textContent = '/';
        slash.dataset.planCrumb = '';
        const strong = doc.createElement('strong');
        strong.textContent = text;
        strong.dataset.planCrumb = '';
        after.after(slash, strong);
        after = strong;
    }
}

export function clearPlanCrumbs(doc) {
    delete doc.getElementById?.('header-task')?.dataset?.planCrumbParent;
    doc.querySelectorAll('#development-task-header [data-plan-crumb]').forEach((node) => node.remove());
}
