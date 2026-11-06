// Browser history for plans: a deep-linked plan gets its board Kanban behind it (browser Back).
// The in-page header Back button was removed in Round 15; the view switcher's Kanban icon is the way back.

// For a plan opened by deep link, put the board Kanban behind it in history so the
// browser Back button lands on the Kanban as well.
export function seedPlanHistory(history, location, kanbanPath) {
    const plan = `${location.pathname}${location.search}${location.hash}`;
    history.replaceState({}, '', kanbanPath);
    history.pushState({}, '', plan);
}
