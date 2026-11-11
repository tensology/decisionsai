import { createBashTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Keep the SDK's streaming, cancellation, output bounds and structured exit
// receipt. Only change shell semantics: a failed check cannot become success
// because its output was piped to tail, or a later command happened to pass.
export default function (pi: ExtensionAPI) {
    const bash = createBashTool(process.cwd(), {
        spawnHook: ({ command, cwd, env }) => ({ command: `set -e -o pipefail\n${command}`, cwd, env }),
    });
    pi.registerTool({
        ...bash,
        execute: async (id, params, signal, onUpdate) => {
            const result = await bash.execute(id, params, signal, onUpdate);
            return { ...result, details: { ...result.details, pipefail: true, errexit: true } };
        },
    });
}
