// A failed save retains the editor and prevents the original navigation event.
export function installNavigationGuard(planning, root, document, window) {
    let replaying = false;
    const click = async (event) => {
        const target = event.target.closest('button, a');
        if (replaying || !target || root.contains(target) || !planning.hasUnsavedChanges()) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!(await planning.beforeLeave()) || !target.isConnected) return;
        replaying = true;
        try {
            target.click();
        } finally {
            replaying = false;
        }
    };
    const unload = (event) => {
        if (!planning.hasUnsavedChanges()) return;
        event.preventDefault();
        event.returnValue = '';
    };
    document.addEventListener('click', click, true);
    window.addEventListener('beforeunload', unload);
    return () => {
        document.removeEventListener('click', click, true);
        window.removeEventListener('beforeunload', unload);
    };
}
