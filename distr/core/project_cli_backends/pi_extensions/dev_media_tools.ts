import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Development-agent media tools. Each call shells into the DecisionsAI Python
// dispatcher (distr/core/workflow/dev_agent_tools.py), which reuses the app's
// configured Image / Vision / Video (Pixazo) settings. computer_use is not
// registered here: it is disabled for development threads (scaffold only).
const PY = process.env.DECISIONS_PYTHON || "python3";
const REPO = process.env.DECISIONS_REPO_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");

async function call(pi: ExtensionAPI, tool: string, params: unknown, signal?: AbortSignal) {
	const result = await pi.exec(PY, ["-m", "distr.core.workflow.dev_agent_tools", tool, JSON.stringify(params ?? {})], {
		cwd: REPO || process.cwd(),
		signal,
		env: { ...process.env, DECISIONS_PROJECT_FOLDER: process.cwd() },
	} as any);
	let parsed: any;
	try {
		parsed = JSON.parse(String(result.stdout || "").trim().split("\n").pop() || "{}");
	} catch {
		parsed = { ok: false, summary: String(result.stderr || result.stdout || "tool failed").slice(0, 2000), artifacts: [] };
	}
	const content: any[] = [{ type: "text", text: parsed.summary || (parsed.ok ? "done" : "failed") }];
	for (const a of parsed.artifacts || []) {
		if (a.kind === "image" && a.bytes < 8 * 1024 * 1024) {
			try {
				content.push({ type: "image", data: readFileSync(a.path).toString("base64"), mimeType: a.mime || "image/png" });
			} catch {}
		}
	}
	return { content, details: parsed };
}

// Tools registered explicitly below; any other tool exposed by
// dev_agent_tools.exposed_tools() (e.g. calendar) is registered generically.
const STATIC_TOOLS = new Set(["image_generate", "vision_analyze", "video_generate"]);

function extraSchemas(): Record<string, any> {
	try {
		const out = execFileSync(PY, ["-m", "distr.core.workflow.dev_agent_tools", "schemas"], {
			cwd: REPO || process.cwd(), encoding: "utf8", timeout: 20000, maxBuffer: 1024 * 1024,
		});
		return JSON.parse(String(out).trim().split("\n").pop() || "{}");
	} catch {
		return {};
	}
}

export default function (pi: ExtensionAPI) {
	for (const [name, schema] of Object.entries(extraSchemas())) {
		if (STATIC_TOOLS.has(name)) continue;
		pi.registerTool({
			name,
			label: name,
			description: String(schema.description || name),
			promptSnippet: String(schema.description || name).slice(0, 120),
			parameters: Type.Unsafe(schema.parameters || { type: "object" }),
			async execute(_id: string, params: unknown, signal?: AbortSignal) { return call(pi, name, params, signal); },
		} as any);
	}
	pi.registerTool({
		name: "image_generate",
		label: "Generate image",
		description: "Generate an image with the configured Image model; saved under .decisions/dev-artifacts/.",
		promptSnippet: "Generate an image asset from a prompt",
		parameters: Type.Object({
			prompt: Type.String(),
			filename: Type.Optional(Type.String()),
			style: Type.Optional(Type.String()),
		}),
		async execute(_id, params, signal) { return call(pi, "image_generate", params, signal); },
	});
	pi.registerTool({
		name: "vision_analyze",
		label: "Analyze image",
		description: "Describe or answer a question about an image file using the configured Vision model.",
		promptSnippet: "Look at an image (screenshot, mockup, attachment) and describe it",
		parameters: Type.Object({
			image_path: Type.String(),
			question: Type.Optional(Type.String()),
		}),
		async execute(_id, params, signal) { return call(pi, "vision_analyze", params, signal); },
	});
	pi.registerTool({
		name: "video_generate",
		label: "Generate video",
		description: "Generate a short Pixazo video. Text-to-video, or pass start_image + end_image for first/last-frame interpolation (duration seconds).",
		promptSnippet: "Generate a short video clip (optionally from start and end frames)",
		parameters: Type.Object({
			prompt: Type.String(),
			start_image: Type.Optional(Type.String()),
			end_image: Type.Optional(Type.String()),
			duration: Type.Optional(Type.Integer({ minimum: 2, maximum: 30 })),
			frame_model: Type.Optional(Type.String()),
			filename: Type.Optional(Type.String()),
		}),
		async execute(_id, params, signal) { return call(pi, "video_generate", params, signal); },
	});
}
