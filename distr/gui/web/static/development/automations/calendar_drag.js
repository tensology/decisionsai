// calendar 2026-10-22: pure helpers for the Automations time grid and drag-to-reschedule.
// No DOM access, so tests can import this module directly with node.
export const SNAP_MINUTES = 15;

export function snapMinutes(minutes, step = SNAP_MINUTES) {
    const snapped = Math.round(Number(minutes) / step) * step;
    return Math.max(0, Math.min(24 * 60 - step, snapped));
}
export function timeToMinutes(time) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(time || ''));
    return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
}
export function minutesToTime(minutes) {
    const v = Math.max(0, Math.min(24 * 60 - 1, Math.round(minutes)));
    return `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
}
export function topForMinutes(minutes, hourPx) {
    return (minutes * hourPx) / 60;
}

// Group by rendered footprint, including nearby start times, so readable chips
// never collide. Keep every occurrence in the group for the expanded list.
export function groupCalendarEvents(events, hourPx, height = 36, gap = 4) {
    const groups = [];
    for (const event of [...events].sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time))) {
        const top = topForMinutes(timeToMinutes(event.time), hourPx);
        const previous = groups[groups.length - 1];
        if (previous && top < previous.end + gap) {
            previous.events.push(event);
            previous.end = top + height;
        } else groups.push({ top, end: top + height, events: [event] });
    }
    return groups;
}
function parseKey(dayKey) {
    const [y, mo, d] = String(dayKey).split('-').map(Number);
    return new Date(y, mo - 1, d);
}
function csv(value) {
    return String(value ?? '').split(',').map((x) => x.trim()).filter(Boolean);
}

// Move one occurrence (fromDayKey @ fromTime) to (toDayKey @ toTime).
// Recurring series shift their time (weekly also its weekday, monthly its day,
// yearly its month+day). Returns the new schedule, or null when the kind can't be dragged.
export function shiftSchedule(schedule, fromDayKey, fromTime, toDayKey, toTime) {
    const s = { ...(schedule || {}) };
    const kind = String(s.kind || 'daily');
    const to = parseKey(toDayKey);
    const from = parseKey(fromDayKey);
    if (!/^\d{2}:\d{2}$/.test(toTime)) return null;
    const times = csv(s.time);
    const replaceTime = () => {
        if (times.length > 1) {
            const next = times.map((t) => (t.padStart(5, '0') === String(fromTime).padStart(5, '0') ? toTime : t.padStart(5, '0')));
            return [...new Set(next)].sort().join(',');
        }
        return toTime;
    };
    if (kind === 'daily') return { ...s, time: replaceTime() };
    if (kind === 'weekly') {
        const days = csv(s.days).map((d) => (Number(d) === 7 ? 0 : Number(d)));
        const moved = days.map((d) => (d === from.getDay() ? to.getDay() : d));
        return { ...s, time: replaceTime(), days: [...new Set(moved)].sort((a, b) => a - b).join(',') };
    }
    if (kind === 'monthly') {
        const days = csv(s.days).map(Number).map((d) => (d === from.getDate() ? to.getDate() : d));
        return { ...s, time: replaceTime(), days: [...new Set(days)].sort((a, b) => a - b).join(',') };
    }
    if (kind === 'yearly') return { ...s, time: toTime, days: String(to.getDate()), month: String(to.getMonth() + 1) };
    if (kind === 'once') return { ...s, run_at: `${toDayKey}T${toTime}` };
    return null;
}
