// Remembers which finished Development threads this browser has already shown.
//
// The project folder turns orange while a thread under it has a completed, failed,
// or blocked run that nobody has opened yet (threadIsReadyUnaddressed in app.js,
// backed by chats.ready_acknowledged_at). Opening a thread POSTs acknowledge-ready,
// but when that write fails (2026-11-12: seeded tz-aware stamps raised a TypeError
// server-side) the folder stayed orange forever. Viewing the thread here records a
// local "seen" stamp so the folder returns to its default colour once every ready
// thread under it has been opened, even before the server stamp lands. A run that
// finishes after the stamp is newer than it, so the folder lights up again.

export const READY_SEEN_STORAGE_KEY = 'decisions-development-ready-seen';
const MAX_ENTRIES = 500;

// Same shape as the server's utc_now_naive().isoformat(): UTC wall time, no zone.
// app.js compares it with naive run completed_at values the same way, so both sides
// share one clock convention.
export function naiveUtcStamp(date = new Date()) {
    return date.toISOString().replace('Z', '');
}

function parse(value) {
    const ms = Date.parse(String(value || ''));
    return Number.isNaN(ms) ? null : ms;
}

function laterStamp(first, second) {
    if (!first) return second || null;
    if (!second) return first;
    const a = parse(first), b = parse(second);
    if (a == null) return second;
    if (b == null) return first;
    return b > a ? second : first;
}

export function createReadySeen(storage = globalThis.window?.localStorage) {
    let seen = {};
    try {
        const raw = JSON.parse(storage?.getItem(READY_SEEN_STORAGE_KEY) || '{}');
        if (raw && typeof raw === 'object' && !Array.isArray(raw)) seen = raw;
    } catch (_) {
        seen = {};
    }

    function save() {
        const ids = Object.keys(seen);
        if (ids.length > MAX_ENTRIES) {
            ids.sort((a, b) => (parse(seen[a]) || 0) - (parse(seen[b]) || 0))
                .slice(0, ids.length - MAX_ENTRIES)
                .forEach((id) => delete seen[id]);
        }
        try {
            storage?.setItem(READY_SEEN_STORAGE_KEY, JSON.stringify(seen));
        } catch (_) {
            /* Private mode or quota: keep the in-memory record for this page. */
        }
    }

    // The chat as the folder should judge it: its acknowledgement is the later of the
    // server stamp and this browser's last view.
    function withSeen(chat) {
        const id = Number(chat?.id || 0);
        const local = id ? seen[id] : null;
        if (!local) return chat;
        const server = Object.prototype.hasOwnProperty.call(chat, 'ready_acknowledged_at') ? chat.ready_acknowledged_at : undefined;
        const effective = laterStamp(server, local);
        return effective === server ? chat : { ...chat, ready_acknowledged_at: effective };
    }

    function markSeen(chat, now = new Date()) {
        const id = Number(chat?.id || 0);
        if (!id) return;
        seen[id] = naiveUtcStamp(now);
        save();
    }

    // Drop local stamps the server has caught up with.
    function prune(chats) {
        let changed = false;
        for (const chat of chats || []) {
            const id = Number(chat?.id || 0);
            const local = id ? seen[id] : null;
            const server = chat?.ready_acknowledged_at;
            const localMs = parse(local), serverMs = parse(server);
            if (local && server && localMs != null && serverMs != null && serverMs >= localMs) {
                delete seen[id];
                changed = true;
            }
        }
        if (changed) save();
    }

    return { withSeen, markSeen, prune, entries: () => ({ ...seen }) };
}
