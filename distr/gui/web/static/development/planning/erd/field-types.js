// The one list of field types the ERD editor offers (Data tab TYPE dropdown). A field's type is
// stored verbatim (e.g. "varchar(255)", "decimal(12,2)", "VARCHAR2(30)"); this module only splits it
// for the dropdown + parameter input and never rewrites a type the user did not change.
export const FIELD_TYPE_GROUPS = [
    ['Integer', ['int', 'smallint', 'bigint', 'serial', 'bigserial']],
    ['Number', ['decimal', 'numeric', 'float', 'double', 'real', 'money']],
    ['Text', ['varchar', 'char', 'text', 'string']],
    ['Date & time', ['date', 'time', 'datetime', 'timestamp', 'timestamptz', 'interval']],
    ['Other', ['boolean', 'uuid', 'json', 'jsonb', 'blob', 'bytea', 'enum']],
];
export const FIELD_TYPES = FIELD_TYPE_GROUPS.flatMap(([, types]) => types);
// Types whose usual form takes a parameter: varchar(255), decimal(12,2), enum(a,b) …
export const PARAM_TYPES = new Set(['varchar', 'char', 'decimal', 'numeric', 'float', 'time', 'timestamp', 'timestamptz', 'enum']);
export const PARAM_HINT = { varchar: '255', char: '1', decimal: '12,2', numeric: '12,2', float: '53', time: '6', timestamp: '6', timestamptz: '6', enum: 'a,b' };

const TYPE_RE = /^([A-Za-z_]\w*)\s*(?:\((.*)\))?$/;
// Mermaid's erDiagram attribute type token: anything else (a comma, a space …) must not be emitted raw.
export const MERMAID_TYPE_RE = /^[A-Za-z_][\w\-[\]()]*$/;

/** "varchar(255)" -> { base: 'varchar', param: '255', known: true }; unknown -> { known: false }. */
export function splitType(type) {
    const value = String(type ?? '').trim();
    const m = TYPE_RE.exec(value);
    if (!m || !FIELD_TYPES.includes(m[1].toLowerCase())) return { base: '', param: '', known: false, value };
    if (m[2] !== undefined && !PARAM_TYPES.has(m[1].toLowerCase())) return { base: '', param: '', known: false, value };
    return { base: m[1].toLowerCase(), raw: m[1], param: m[2] ?? '', known: true, value };
}
export function joinType(base, param) {
    const p = String(param ?? '').trim().replace(/^\(|\)$/g, '');
    return p && PARAM_TYPES.has(base) ? `${base}(${p})` : base;
}
/** Token written to Mermaid for a type: the type itself when Mermaid accepts it, else its safe base. */
export function mermaidType(type) {
    const value = String(type || 'string').trim().replace(/\s+/g, '_');
    if (MERMAID_TYPE_RE.test(value)) return value;
    const base = /^[A-Za-z_]\w*/.exec(value)?.[0];
    return base || 'string';
}
