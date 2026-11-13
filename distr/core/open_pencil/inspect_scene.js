// DecisionsAI read-only OpenPencil scene inspection.
// Run via `openpencil eval <file> --stdin --json` WITHOUT --write/--output: nothing is saved.
// __DECISIONS_LIMITS__ is replaced by the Python bridge with {"maxDepth": N, "maxNodes": N}.
const LIMITS = __DECISIONS_LIMITS__
let emitted = 0
let truncated = false
const safe = (fn, fallback) => { try { const v = fn(); return v === undefined ? fallback : v } catch (e) { return fallback } }
const nearest = (node, types) => {
  let p = safe(() => node.parent, null)
  while (p && p.type !== 'PAGE' && p.type !== 'DOCUMENT') {
    if (types.includes(p.type)) return p
    p = safe(() => p.parent, null)
  }
  return null
}
const boundFields = (node) => Object.keys(safe(() => node.boundVariables, null) || {})
function tree(node, depth) {
  emitted += 1
  const kids = safe(() => node.children, null)
  const entry = { id: node.id, name: node.name, type: node.type }
  const bound = boundFields(node)
  if (bound.length) entry.bound = bound
  if (kids) {
    entry.childCount = kids.length
    if (depth >= LIMITS.maxDepth || emitted >= LIMITS.maxNodes) {
      if (kids.length) truncated = true
    } else {
      entry.children = []
      for (const child of kids) {
        if (emitted >= LIMITS.maxNodes) { truncated = true; break }
        entry.children.push(tree(child, depth + 1))
      }
    }
  }
  return entry
}
const out = { pages: [], frames: [], components: [], slots: [], instances: [], bindings: [], textInComponents: [], variables: null, truncated: false }
for (const page of figma.root.children) {
  const top = safe(() => page.children, [])
  out.pages.push({ id: page.id, name: page.name, topLevelCount: top.length, layers: top.map((n) => tree(n, 1)) })
  for (const n of page.findAll(() => true)) {
    const parentType = safe(() => n.parent.type, null)
    if (n.type === 'FRAME' && (parentType === 'PAGE' || parentType === 'SECTION')) {
      out.frames.push({ id: n.id, name: n.name, page: page.name, width: safe(() => n.width, null), height: safe(() => n.height, null), status: safe(() => openpencil.getStatus(n), null) })
    }
    if (n.type === 'COMPONENT' || n.type === 'COMPONENT_SET') {
      const defs = safe(() => n.componentPropertyDefinitions, {}) || {}
      const props = Object.entries(defs).map(([key, d]) => ({ key, type: d.type, defaultValue: d.defaultValue === undefined ? null : d.defaultValue, variantOptions: d.variantOptions || null, description: d.description || null, slotSettings: d.slotSettings || null }))
      const behaviour = safe(() => openpencil.getBehaviour(n), null)
      out.components.push({
        id: n.id, name: n.name, type: n.type, page: page.name, parentType,
        isVariant: parentType === 'COMPONENT_SET',
        description: safe(() => n.description, '') || '',
        properties: props,
        behaviour: behaviour ? { kind: safe(() => behaviour.kind, null), missing: safe(() => behaviour.missing, null) } : null,
        status: safe(() => openpencil.getStatus(n), null),
      })
    }
    if (n.type === 'SLOT') {
      const owner = nearest(n, ['INSTANCE', 'COMPONENT', 'COMPONENT_SET'])
      const kids = safe(() => n.children, []) || []
      out.slots.push({
        id: n.id, name: n.name, page: page.name, childCount: kids.length,
        ownerId: owner ? owner.id : null, ownerName: owner ? owner.name : null, ownerType: owner ? owner.type : null,
        limitViolations: safe(() => n.limitViolations, null),
      })
    }
    if (n.type === 'INSTANCE') {
      const main = safe(() => n.mainComponent, null)
      out.instances.push({ id: n.id, name: n.name, page: page.name, mainComponentId: main ? main.id : null, mainComponentName: main ? main.name : null })
    }
    const bound = boundFields(n)
    if (bound.length) out.bindings.push({ id: n.id, name: n.name, type: n.type, page: page.name, fields: bound })
    if (n.type === 'TEXT') {
      const comp = nearest(n, ['COMPONENT'])
      if (comp && !nearest(n, ['INSTANCE'])) {
        out.textInComponents.push({ id: n.id, name: n.name, componentId: comp.id, componentName: comp.name, page: page.name, bound, characters: String(safe(() => n.characters, '')).slice(0, 80) })
      }
    }
  }
}
const vars = safe(() => figma.variables.getLocalVariables(), null)
const cols = safe(() => figma.variables.getLocalVariableCollections(), null)
out.variables = vars === null ? null : { count: vars.length, collections: cols ? cols.map((c) => ({ id: c.id, name: c.name })) : [], names: vars.slice(0, 200).map((v) => ({ id: v.id, name: v.name, type: safe(() => v.resolvedType, null) })) }
out.truncated = truncated
out.emittedLayerNodes = emitted
return out
