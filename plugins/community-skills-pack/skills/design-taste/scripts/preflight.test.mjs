// scripts/preflight.test.mjs
//
// Zero-dependency test suite for scripts/preflight.mjs plus repo-wide
// hygiene checks (cite resolution, U+2014 count, plugin manifest,
// upstream-drift script). Run with: node --test scripts/
//
// ponytail: no test framework, no fixtures library. node:test + node:assert
// is the whole toolbox this project needs.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, mkdtempSync, writeFileSync, rmSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const ROOT = join(import.meta.dirname, "..");
const SCRIPT = join(ROOT, "scripts", "preflight.mjs");

function run(args) {
  try {
    const stdout = execFileSync("node", [SCRIPT, ...args], { cwd: ROOT, encoding: "utf8" });
    return { stdout, status: 0 };
  } catch (err) {
    return { stdout: err.stdout ?? "", status: err.status };
  }
}

function withTempFile(content, ext) {
  const dir = mkdtempSync(join(tmpdir(), "preflight-test-"));
  const path = join(dir, `fixture${ext}`);
  writeFileSync(path, content, "utf8");
  return { dir, path };
}

// Slice of content between a `## <heading>` line and the next `^## ` heading
// (or end of file). Used by both the Core self-containment test and the
// routing-table cite test so they scope to exactly the same section.
function sectionSlice(content, heading) {
  const headings = [...content.matchAll(/^## .*$/gm)];
  const idx = headings.findIndex((m) => m[0].startsWith(heading));
  if (idx < 0) return null;
  const start = headings[idx].index + headings[idx][0].length;
  const end = idx + 1 < headings.length ? headings[idx + 1].index : content.length;
  return content.slice(start, end);
}

// First markdown table (contiguous run of `|`-prefixed lines) after `heading`.
function firstTableAfter(content, heading) {
  const idx = content.indexOf(heading);
  if (idx < 0) return [];
  const rows = [];
  let started = false;
  for (const line of content.slice(idx).split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("|")) {
      started = true;
      rows.push(trimmed);
    } else if (started) {
      break;
    }
  }
  return rows;
}

function parseTableRow(row) {
  return row
    .split("|")
    .slice(1, -1)
    .map((c) => c.trim());
}

// -- (a) each HARD rule fires on dirty.html, exit 1 --------------------

test("dirty.html fixture: every HARD rule fires, exit 1", () => {
  const { stdout, status } = run(["scripts/fixtures/dirty.html"]);
  assert.equal(status, 1);
  for (const rule of [
    "TRANSITION_ALL",
    "OUTLINE_NONE_NO_FOCUS_VISIBLE",
    "Z_INDEX_999",
    "SCALE_ZERO_ENTRY",
    "EM_DASH",
  ]) {
    assert.match(stdout, new RegExp(rule), `expected ${rule} to be reported`);
  }
});

// -- (b) clean.html exits 0 --------------------------------------------

test("clean.html fixture: no violations, exit 0", () => {
  const { stdout, status } = run(["scripts/fixtures/clean.html"]);
  assert.equal(status, 0);
  assert.match(stdout, /0 hard violation\(s\)/);
});

// -- (c) .md files only run the dash rules ------------------------------

test(".md files skip code-authoring rules but still catch em dash", () => {
  const dirtyCode = [
    ".card { transition: all 0.3s ease; }",
    ".card { outline: none; }",
    ".modal { z-index: 999; }",
    ".enter { transform: scale(0); }",
    ".hero { height: 100vh; }",
  ].join("\n");

  const { dir: dir1, path: codeOnly } = withTempFile(dirtyCode, ".md");
  try {
    const { stdout, status } = run([codeOnly]);
    assert.equal(status, 0, "code-authoring patterns must not fire in .md");
    assert.doesNotMatch(stdout, /TRANSITION_ALL|OUTLINE_NONE|Z_INDEX_999|SCALE_ZERO_ENTRY|VIEWPORT_UNIT/);
  } finally {
    rmSync(dir1, { recursive: true, force: true });
  }

  const dashInMd = `Headline uses an em dash ${String.fromCharCode(0x2014)} here.\n`;
  const { dir: dir2, path: dashOnly } = withTempFile(dashInMd, ".md");
  try {
    const { stdout, status } = run([dashOnly]);
    assert.equal(status, 1, "em dash must still fire in .md");
    assert.match(stdout, /EM_DASH/);
  } finally {
    rmSync(dir2, { recursive: true, force: true });
  }
});

// -- (d) digit-endash-digit not flagged; space-padded endash warned ----

test("en dash: digit-endash-digit is clean, space-padded endash warns", () => {
  const EN_DASH = String.fromCharCode(0x2013);
  const content = [
    `Typography scale runs 65${EN_DASH}75ch wide.`,
    `The range is 2019 ${EN_DASH} 2026 for reference.`,
  ].join("\n");
  const { dir, path } = withTempFile(content, ".html");
  try {
    const { stdout, status } = run([path]);
    assert.equal(status, 0, "en dash warnings never affect exit code");
    assert.doesNotMatch(stdout, new RegExp(`:1\\s+WARN:EN_DASH_SEPARATOR`), "digit-endash-digit must not be flagged");
    assert.match(stdout, /:2\s+WARN:EN_DASH_SEPARATOR/, "space-padded endash must be flagged");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// -- (d2) Tailwind / JSX-object / HTML-entity spellings are caught ---------

test("Tailwind, JSX-object and HTML-entity spellings of banned patterns are caught", () => {
  const content = [
    '<div className="transition-all outline-none z-[9999] scale-0" />',
    "<motion.div initial={{ scale: 0 }} style={{ zIndex: 9999 }} />",
    "<p>Entity em dash &mdash; here</p>",
  ].join("\n");
  const { dir, path } = withTempFile(content, ".tsx");
  try {
    const { stdout, status } = run([path]);
    assert.equal(status, 1);
    // OUTLINE_NONE_NO_FOCUS_VISIBLE is intentionally not asserted here: its
    // HARD-vs-WARN verdict now depends on file-wide focus-visible context,
    // which the dedicated "outline semantics" test below covers exhaustively.
    // This test's job is spelling coverage (Tailwind/JS-object/HTML-entity),
    // not outline verdict semantics.
    for (const rule of ["TRANSITION_ALL", "Z_INDEX_999", "SCALE_ZERO_ENTRY", "EM_DASH"]) {
      assert.match(stdout, new RegExp(rule), `expected ${rule} to be reported`);
    }
    assert.equal((stdout.match(/Z_INDEX_999/g) ?? []).length, 2, "z-[9999] and zIndex: 9999");
    assert.equal((stdout.match(/SCALE_ZERO_ENTRY/g) ?? []).length, 2, "scale-0 and scale: 0");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// -- (d3) font-family count dedupes spellings of the same family ----------

test("font-family count: quoted/stacked spellings of one family count once", () => {
  const content = [
    'h1 { font-family: "Geist", sans-serif; }',
    "p { font-family: Geist; }",
    "code { font-family: 'Geist Mono', monospace; }",
    '<p style="font-family: Inter">x</p>',
  ].join("\n");
  const { dir, path } = withTempFile(content, ".html");
  try {
    const { stdout } = run([path]);
    assert.doesNotMatch(stdout, /FONT_FAMILY_COUNT/, "3 families must not warn");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// -- (e) self-scan of the shipped skill files exits 0 -------------------

test("self-scan of SKILL.md README.md reference/ exits 0", () => {
  const { stdout, status } = run(["SKILL.md", "README.md", "reference/"]);
  assert.equal(status, 0, stdout);
  assert.match(stdout, /0 hard violation\(s\)/);
});

// -- cite resolution: every "Section N" / "§N" resolves to a heading ----

function collectHeadingNumbers(content) {
  const numbers = new Set();
  for (const line of content.split("\n")) {
    const m = line.match(/^#{1,6}\s+§?(\d+)(?:\.([A-Za-z0-9]+))?\b/);
    if (!m) continue;
    const major = m[1];
    numbers.add(major);
    if (m[2]) numbers.add(`${major}.${m[2]}`);
  }
  return numbers;
}

function collectCites(content) {
  // SKILL.md's "Section index" table rows are the legend that DEFINES the
  // §N -> file mapping (including which numbers have no local heading,
  // e.g. "§6 -> this file's Motion bullets", "§10-13 -> Not ported").
  // Those rows are declarations, not cross-references that must resolve
  // to a heading, so table rows ("| ... |") are excluded from the check.
  const cites = [];
  const re = /Section (\d+(?:\.[A-Za-z0-9]+)?)|§(\d+(?:\.[A-Za-z0-9]+)?)/g;
  for (const line of content.split("\n")) {
    if (line.trim().startsWith("|")) continue;
    let m;
    while ((m = re.exec(line))) {
      cites.push(m[1] ?? m[2]);
    }
  }
  return cites;
}

test("every Section N / §N cite resolves to an existing heading", () => {
  const files = ["SKILL.md", "reference/anti-slop.md", "reference/core-rules.md", "reference/design-systems.md", "reference/motion.md", "reference/pre-flight.md"];
  const allHeadingNumbers = new Set();
  const contents = {};
  for (const f of files) {
    const content = readFileSync(join(ROOT, f), "utf8");
    contents[f] = content;
    for (const n of collectHeadingNumbers(content)) allHeadingNumbers.add(n);
  }

  // SKILL.md's own Section index explicitly documents §10/§12/§13 as not
  // ported from upstream. Those are acknowledged gaps, not broken cites.
  const notPortedLine = contents["SKILL.md"].split("\n").find((l) => l.includes("Not ported"));
  const notPorted = new Set(
    notPortedLine ? [...notPortedLine.matchAll(/§(\d+)/g)].map((m) => m[1]) : []
  );

  const unresolved = [];
  for (const [file, content] of Object.entries(contents)) {
    for (const cite of collectCites(content)) {
      const major = cite.split(".")[0];
      // Exact match only: "Section 4.3" must not pass just because 4.1 exists.
      const resolved = allHeadingNumbers.has(cite) || notPorted.has(major);
      if (!resolved) unresolved.push(`${file}: Section ${cite}`);
    }
  }

  assert.deepEqual(unresolved, []);
});

test("every reference/*.md path mentioned resolves to an existing file", () => {
  const files = ["SKILL.md", "README.md", "reference/anti-slop.md", "reference/core-rules.md", "reference/design-systems.md", "reference/motion.md", "reference/pre-flight.md"];
  const missing = [];
  for (const f of files) {
    const content = readFileSync(join(ROOT, f), "utf8");
    const re = /reference\/[A-Za-z0-9_-]+\.md/g;
    let m;
    while ((m = re.exec(content))) {
      const target = join(ROOT, m[0]);
      if (!existsSync(target)) missing.push(`${f}: ${m[0]}`);
    }
  }
  assert.deepEqual(missing, []);
});

// -- pre-flight split: Core is self-contained, stack tokens live only in the
// -- React / Next addendum ---------------------------------------------

test("pre-flight.md: Universal Core is self-contained; stack tokens live only in Addendum: React / Next", () => {
  const content = readFileSync(join(ROOT, "reference", "pre-flight.md"), "utf8");

  const coreSlice = sectionSlice(content, "## Pre-Flight: Universal Core");
  assert.ok(coreSlice !== null, "Universal Core heading missing");
  for (const token of [
    "nav height",
    "80px",
    "hero",
    "Hero",
    "logo wall",
    "marquee",
    "bento",
    "useScroll",
    "GSAP",
    "use client",
    "useEffect",
  ]) {
    assert.ok(!coreSlice.includes(token), `${token} found inside Universal Core`);
  }

  const stackIndex = content.indexOf("## Addendum: React / Next");
  assert.ok(stackIndex > 0, "Addendum: React / Next heading missing");
  const beforeStack = content.slice(0, stackIndex);
  for (const token of ["use client", "useEffect", "GSAP", "leading-[", "pb-1", "h-screen", "min-h-["]) {
    assert.ok(!beforeStack.includes(token), `${token} found outside Addendum: React / Next`);
  }
});

// -- SKILL.md routing table: four mode rows, all load pre-flight.md, and ---
// -- every Core cite resolves inside the intersection of their Load cells --

test("SKILL.md Routing table: four mode rows, every row loads pre-flight.md, Core cites resolve within the intersection", () => {
  const skillContent = readFileSync(join(ROOT, "SKILL.md"), "utf8");
  const rows = firstTableAfter(skillContent, "## Routing");
  assert.ok(rows.length > 2, "no routing table found after ## Routing");
  const dataRows = rows.slice(2).map(parseTableRow); // drop header + separator row
  assert.equal(dataRows.length, 4, `expected exactly 4 mode rows, got ${dataRows.length}`);

  const modeNames = dataRows.map((cells) => cells[0].replace(/\*/g, "").trim());
  assert.deepEqual(modeNames, ["Persuade", "Operate", "Read", "Experience"]);

  const refRe = /reference\/[A-Za-z0-9_-]+\.md/g;
  const loadCell = dataRows.map((cells) => cells[3] ?? "");
  const perRowFiles = loadCell.map((cell) => new Set([...cell.matchAll(refRe)].map((m) => m[0])));
  perRowFiles.forEach((files, i) => {
    assert.ok(files.has("reference/pre-flight.md"), `${modeNames[i]} row must load reference/pre-flight.md`);
  });

  let intersection = perRowFiles[0];
  for (const files of perRowFiles.slice(1)) {
    intersection = new Set([...intersection].filter((f) => files.has(f)));
  }

  const preFlightContent = readFileSync(join(ROOT, "reference", "pre-flight.md"), "utf8");
  const coreSlice = sectionSlice(preFlightContent, "## Pre-Flight: Universal Core");
  assert.ok(coreSlice !== null, "Universal Core heading missing");

  const allowedHeadingNumbers = new Set();
  for (const f of intersection) {
    const content = readFileSync(join(ROOT, f), "utf8");
    for (const n of collectHeadingNumbers(content)) allowedHeadingNumbers.add(n);
  }

  const unresolved = [];
  for (const cite of collectCites(coreSlice)) {
    const major = cite.split(".")[0];
    if (!allowedHeadingNumbers.has(cite) && !allowedHeadingNumbers.has(major)) unresolved.push(cite);
  }
  assert.deepEqual(unresolved, []);
});

// -- z-index semantics: magic-number family hard-fails, scaled values pass -

test("z-index semantics: magic 999/9999 hard-fails, a --z- token silences the warning, unscaled >=1000 warns", () => {
  const scaled = [":root { --z-modal: 1400; }", ".tooltip { z-index: 1400; }"].join("\n");
  const { dir: dir1, path: path1 } = withTempFile(scaled, ".css");
  try {
    const { stdout, status } = run([path1]);
    assert.equal(status, 0, stdout);
    assert.doesNotMatch(stdout, /Z_INDEX_999|Z_INDEX_UNSCALED/, "a --z- token must silence the warning");
  } finally {
    rmSync(dir1, { recursive: true, force: true });
  }

  const unscaled = ".tooltip { z-index: 1400; }\n";
  const { dir: dir2, path: path2 } = withTempFile(unscaled, ".css");
  try {
    const { stdout, status } = run([path2]);
    assert.equal(status, 0, stdout);
    assert.match(stdout, /WARN:Z_INDEX_UNSCALED/);
  } finally {
    rmSync(dir2, { recursive: true, force: true });
  }

  const magic = ".modal { z-index: 9999; }\n";
  const { dir: dir3, path: path3 } = withTempFile(magic, ".css");
  try {
    const { stdout, status } = run([path3]);
    assert.equal(status, 1);
    assert.match(stdout, /Z_INDEX_999/);
  } finally {
    rmSync(dir3, { recursive: true, force: true });
  }
});

// -- outline semantics: per-selector escape hatch, demoted to WARN when the -
// -- ring is not in the file at all -------------------------------------

test("outline semantics: per-selector focus-visible escape hatch; WARN when the ring is elsewhere or absent", () => {
  const sameAttribute = '<button className="outline-none focus-visible:ring-2">Go</button>\n';
  const { dir: dir1, path: path1 } = withTempFile(sameAttribute, ".tsx");
  try {
    const { stdout, status } = run([path1]);
    assert.equal(status, 0, stdout);
  } finally {
    rmSync(dir1, { recursive: true, force: true });
  }

  const differentSelector = [".a { outline: 0px; }", ".b:focus-visible { outline: 2px solid blue; }"].join("\n");
  const { dir: dir2, path: path2 } = withTempFile(differentSelector, ".css");
  try {
    const { stdout, status } = run([path2]);
    assert.equal(status, 1, stdout);
    assert.match(stdout, /OUTLINE_NONE_NO_FOCUS_VISIBLE/);
  } finally {
    rmSync(dir2, { recursive: true, force: true });
  }

  const noRingInFile = ".card { outline: none; }\n";
  const { dir: dir3, path: path3 } = withTempFile(noRingInFile, ".css");
  try {
    const { stdout, status } = run([path3]);
    assert.equal(status, 0, stdout);
    assert.match(stdout, /WARN:OUTLINE_NONE_RING_NOT_IN_FILE/);
  } finally {
    rmSync(dir3, { recursive: true, force: true });
  }
});

// -- the 8 new rules; #000dea stays clean; .ts/.astro scan as code; --------
// -- .mdx is prose-only but still bans U+2014 -----------------------------

test("8 new rules fire on dirty.html; #000dea is clean; .ts/.astro scan as code; .mdx is prose-only", () => {
  const { stdout, status } = run(["scripts/fixtures/dirty.html"]);
  assert.equal(status, 1);
  for (const rule of [
    "SCROLL_LISTENER",
    "GRADIENT_TEXT",
    "SIDE_STRIPE_BORDER",
    "REPEATING_GRADIENT",
    "FE_TURBULENCE",
    "CURSOR_IMAGE",
    "WARN:PURE_BLACK_WHITE",
    "WARN:GOOGLE_FONTS_LINK",
  ]) {
    assert.match(stdout, new RegExp(rule), `expected ${rule} to be reported`);
  }

  const { dir: dir1, path: path1 } = withTempFile(".badge { color: #000dea; }\n", ".css");
  try {
    const { stdout: out1 } = run([path1]);
    assert.doesNotMatch(out1, /PURE_BLACK_WHITE/, "#000dea must not be flagged as pure black/white");
  } finally {
    rmSync(dir1, { recursive: true, force: true });
  }

  const { dir: dir2, path: path2 } = withTempFile("addEventListener('scroll', onScroll);\n", ".ts");
  try {
    const { stdout: out2, status: status2 } = run([path2]);
    assert.equal(status2, 1);
    assert.match(out2, /SCROLL_LISTENER/, ".ts must be scanned as code");
  } finally {
    rmSync(dir2, { recursive: true, force: true });
  }

  const { dir: dir3, path: path3 } = withTempFile(
    "<script>\n  addEventListener('scroll', onScroll);\n</script>\n",
    ".astro"
  );
  try {
    const { stdout: out3, status: status3 } = run([path3]);
    assert.equal(status3, 1);
    assert.match(out3, /SCROLL_LISTENER/, ".astro must be scanned as code");
  } finally {
    rmSync(dir3, { recursive: true, force: true });
  }

  const mdxContent = `A gradient text example: \`background-clip: text\`.\nAn em dash example ${String.fromCharCode(
    0x2014
  )} here.\n`;
  const { dir: dir4, path: path4 } = withTempFile(mdxContent, ".mdx");
  try {
    const { stdout: out4, status: status4 } = run([path4]);
    assert.equal(status4, 1, ".mdx must still fail on U+2014");
    assert.match(out4, /EM_DASH/);
    assert.doesNotMatch(out4, /GRADIENT_TEXT/, ".mdx must not run code-authoring rules");
  } finally {
    rmSync(dir4, { recursive: true, force: true });
  }
});

// -- zero literal U+2014 in shipped prose --------------------------------

test("zero U+2014 (em dash) across SKILL.md, README.md, NOTICE, reference/*.md", () => {
  const EM_DASH = String.fromCharCode(0x2014);
  const files = [
    "SKILL.md",
    "README.md",
    "NOTICE",
    "CHANGELOG.md",
    ...readdirSync(join(ROOT, "reference"))
      .filter((f) => f.endsWith(".md"))
      .map((f) => join("reference", f)),
  ];
  for (const f of files) {
    const content = readFileSync(join(ROOT, f), "utf8");
    const count = content.split(EM_DASH).length - 1;
    assert.equal(count, 0, `${f} has ${count} literal U+2014`);
  }
});

// -- plugin.json is valid JSON with no skills field ----------------------

test(".claude-plugin/plugin.json is valid JSON with no skills field", () => {
  const raw = readFileSync(join(ROOT, ".claude-plugin", "plugin.json"), "utf8");
  const parsed = JSON.parse(raw);
  assert.equal(typeof parsed.name, "string");
  assert.ok(!("skills" in parsed), "plugin.json must not declare a skills field");
});

// -- upstream-drift.sh runs and reports OK/DRIFT/MISSING ------------------

test("scripts/upstream-drift.sh runs and prints OK/DRIFT/MISSING lines", (t) => {
  let stdout;
  try {
    stdout = execFileSync("bash", [join(ROOT, "scripts", "upstream-drift.sh")], {
      cwd: ROOT,
      encoding: "utf8",
      timeout: 20000,
    });
  } catch (err) {
    t.skip(`upstream-drift.sh did not complete (likely offline): ${err.message}`);
    return;
  }
  const lines = stdout.trim().split("\n").filter(Boolean);
  if (lines.length === 0) {
    t.skip("upstream-drift.sh produced no output (likely offline)");
    return;
  }
  for (const line of lines) {
    assert.match(line, /^(OK|DRIFT|MISSING|UNREACHABLE)\s+\S+/, `unexpected line: ${line}`);
  }
});
