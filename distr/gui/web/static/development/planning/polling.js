// One scoped request at a time. Leaving Plan invalidates in-flight responses;
// network failures back off without treating an active server turn as stopped.
export function createPlanPolling({ read, apply, onError, active,
    schedule = setTimeout, cancel = clearTimeout }) {
    let timer = null, generation = 0;
    function stop() {
        generation++;
        if (timer !== null) cancel(timer);
        timer = null;
    }
    function start() {
        stop();
        const token = generation;
        let delay = 1500;
        const valid = () => token === generation && active();
        async function tick() {
            timer = null;
            if (!valid()) return;
            let again = true;
            try {
                const value = await read();
                if (!valid()) return;
                again = await apply(value, valid);
                delay = 1500;
            } catch (error) {
                if (!valid()) return;
                onError(error);
                delay = Math.min(12000, delay * 2);
            }
            if (again && valid()) timer = schedule(tick, delay);
        }
        if (valid()) timer = schedule(tick, delay);
    }
    return { start, stop };
}
