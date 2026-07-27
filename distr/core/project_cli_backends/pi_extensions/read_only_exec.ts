import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { resolve, sep } from "node:path";

const SAFE_COMMANDS = [
	/^node\s+--test(?:\s+[A-Za-z0-9_./*?@:-]+)*$/,
	/^(?:python3?|pytest|\.venv\/bin\/python|\/(?:Users|home)\/[A-Za-z0-9._-]+\/\.virtualenvs\/[A-Za-z0-9._-]+\/bin\/python)\s+(?:-m\s+pytest\s+)?[A-Za-z0-9_./*?=@:+-]*(?:\s+[A-Za-z0-9_./*?=@:+-]+)*$/,
	/^(?:npm|pnpm|yarn)\s+(?:test|run\s+(?:test|lint|typecheck|check))(?:\s+--?[A-Za-z0-9_.:=+-]+)*$/,
	/^npx\s+(?:eslint|tsc|vitest|jest)(?:\s+[A-Za-z0-9_./*?=@:+-]+)*$/,
	/^(?:ruff\s+check|mypy|pyright)(?:\s+[A-Za-z0-9_./*?=@:+-]+)*$/,
	/^git\s+(?:status|diff)(?:\s+[A-Za-z0-9_./*?=@:+-]+)*$/,
];

function isSafe(command: string): boolean {
	const normalized = command.trim().replace(/\s+/g, " ");
	if (!normalized || /[;&|><`$\\\n\r]/.test(normalized)) return false;
	if (/\b(?:--fix|--write|-w|install|add|remove|delete|clean|reset|checkout)\b/i.test(normalized)) return false;
	return SAFE_COMMANDS.some((pattern) => pattern.test(normalized));
}

export default function (pi: ExtensionAPI) {
	const projectRoot = resolve(process.cwd());
	pi.on("tool_call", async (event) => {
		if (!["read", "grep", "find", "ls"].includes(event.toolName)) return undefined;
		const input = event.input as Record<string, unknown>;
		const requested = String(input.path || ".");
		const target = resolve(projectRoot, requested);
		if (target !== projectRoot && !target.startsWith(`${projectRoot}${sep}`)) {
			return {
				block: true,
				reason: `Read-only workflow scope blocks paths outside the active project: ${requested}`,
			};
		}
		return undefined;
	});

	pi.registerTool({
		name: "exec",
		label: "Read-only verification",
		description: "Run an allowlisted non-mutating test, lint, type-check, git status, or git diff command in the project root.",
		promptSnippet: "Run a non-mutating verification command in the current project root",
		promptGuidelines: [
			"Use exec only for the exact non-mutating test, lint, type-check, git status, or git diff command needed as evidence.",
			"The exec tool already runs in the project root; never prefix a command with cd or shell operators.",
			"When the repository requires a project virtualenv, invoke its bin/python directly with -m pytest; do not activate a shell environment.",
		],
		parameters: Type.Object({
			command: Type.String({ description: "One allowlisted verification command, without cd or shell operators" }),
		}),
		async execute(_toolCallId, params, signal) {
			const command = String(params.command || "").trim().replace(/\s+/g, " ");
			if (!isSafe(command)) {
				return {
					content: [{ type: "text", text: "Blocked: command is outside the read-only verification allowlist. Run only the focused test/lint/type-check command without cd or shell operators." }],
					details: { blocked: true, command },
				};
			}
			const result = await pi.exec("/bin/sh", ["-lc", command], {
				cwd: process.cwd(),
				signal,
				timeout: 120_000,
			});
			const combined = `${result.stdout || ""}${result.stderr || ""}`.slice(-80_000);
			return {
				content: [{ type: "text", text: `${combined}\nExit code: ${result.code}`.trim() }],
				details: { command, exitCode: result.code, killed: result.killed },
			};
		},
	});
}
