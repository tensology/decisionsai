// Project play/stop controls own only configured startup terminals.
export function isStartupTerminal(session) {
    return session?.purpose === 'startup';
}
