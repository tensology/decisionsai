import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Same rules as distr/core/secret_redaction.py: agents never read secret files
// (.env*, password files, credentials, keys), and secret values in any tool
// output are masked before the model or the thread log sees them.
const SAFE_ENV_TEMPLATE = /\.(?:example|sample|template|dist|defaults?)$/i;
const SECRET_FILE_NAME = new RegExp(
	"^(?:\\.env(?:[.-][\\w.-]*)?|\\.?(?:pg)?pass(?:word)?s?(?:[.-][\\w.-]*)?|\\.netrc|\\.pgpass|" +
		"id_(?:rsa|dsa|ecdsa|ed25519)(?:\\.\\w+)?|[\\w.-]*\\.(?:pem|key|p12|pfx|keystore|jks)|" +
		"[\\w.-]*credentials?[\\w.-]*|[\\w.-]*(?:secret|passw(?:or)?d|-pass|_pass)[\\w.-]*\\.(?:txt|env|json|ya?ml|ini|cfg|conf)|" +
		"[\\w.-]*(?:secret|passw(?:or)?d|-pass|_pass)[\\w-]*)$",
	"i",
);
const CODE_SUFFIX = /\.(?:py|js|jsx|ts|tsx|html?|css|scss|md|rst|go|rb|php|java|kt|swift|sql|sh)$/i;

export function isSecretFile(path: string): boolean {
	const name = String(path || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
	if (!name || name.endsWith(".pub") || SAFE_ENV_TEMPLATE.test(name)) return false;
	if (CODE_SUFFIX.test(name) || name.toLowerCase().startsWith("test")) return false;
	return SECRET_FILE_NAME.test(name);
}

// Only words that look like paths count, so `git commit -m "fix password reset"` is fine.
export function secretReference(command: string): string {
	const words = String(command || "").split(/[\s'"`;&|<>()=]+/);
	for (const word of words) {
		if (!word || word.startsWith("-")) continue;
		if (/[*?]/.test(word) && /(^|\/)\.env\*?$/.test(word)) return word;
		if (!/[./]/.test(word) && !/^(?:id_\w+)$/.test(word)) continue;
		if (isSecretFile(word)) return word;
	}
	return "";
}

const SECRET_ASSIGNMENT =
	/^(\s*(?:[\w./-]*:\d+:|\d+[:|])?\s*(?:export\s+)?[\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|pwd|dsn|auth)[\w.-]*\s*[:=]\s*)(\S.*)$/gim;
const SECRET_JSON = /(["'][\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|pwd)[\w.-]*["']\s*:\s*)(["'])[^"'\n]*\2/gi;
const SECRET_URL = /\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@]+:)[^\s@/]+@/gi;
const SECRET_TOKEN =
	/\b(?:sk-[a-z0-9_-]{12,}|sk_(?:live|test)_[a-z0-9]{12,}|xox[baprs]-[a-z0-9-]{12,}|gh[pousr]_[a-z0-9]{12,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/gi;

export function redactSecrets(text: string): string {
	let value = String(text || "");
	if (!value) return value;
	value = value.replace(SECRET_TOKEN, "[redacted]");
	value = value.replace(SECRET_URL, "$1[redacted]@");
	value = value.replace(SECRET_JSON, (_m, key, quote) => `${key}${quote}[redacted]${quote}`);
	return value.replace(SECRET_ASSIGNMENT, (_m, key) => `${key}[redacted]`);
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event) => {
		const input = (event.input || {}) as Record<string, unknown>;
		let hit = "";
		if (event.toolName === "bash" || event.toolName === "exec") {
			hit = secretReference(String(input.command || ""));
		} else if (["read", "grep", "find", "ls", "edit", "write"].includes(event.toolName)) {
			const path = String(input.path || input.file_path || "");
			hit = path && isSecretFile(path) ? path : "";
		}
		if (!hit) return undefined;
		return { block: true, reason: `Refused: ${hit} holds secrets and is never read by agents.` };
	});
	pi.on("tool_result", async (event) => {
		let changed = false;
		const content = (event.content || []).map((part) => {
			if (part.type !== "text") return part;
			const text = redactSecrets(part.text);
			if (text !== part.text) changed = true;
			return { ...part, text };
		});
		return changed ? { content } : undefined;
	});
}
