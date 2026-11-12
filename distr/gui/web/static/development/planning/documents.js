// Documents tab (pure helpers, no DOM): which plan items are documents, their tab order and labels,
// which tab is open (always one, FRAC first), and roving-tab keyboard movement.
import { USER_DOCUMENT_TYPE } from './documents-config.js?v=20261103-ifaces3';

export const DOCUMENT_ORDER = ['frac', 'brief', 'prd', 'requirements_pack', 'file_structure', 'decision', USER_DOCUMENT_TYPE];
export const DOCUMENT_TYPES = new Set(DOCUMENT_ORDER);
const LABELS = { frac: 'FRAC', brief: 'Brief', prd: 'PRD', requirements_pack: 'Requirements Pack', file_structure: 'File structure', decision: 'Decision' };

export const isDocument = (item) => DOCUMENT_TYPES.has(String(item?.item_type || ''));
export const isUserDocument = (item) => item?.item_type === USER_DOCUMENT_TYPE;

/** Documents in tab order: by DOCUMENT_ORDER, then the workspace's own order (sort_order, id). Nothing is dropped. */
export function orderDocuments(items) {
    const rank = (t) => DOCUMENT_ORDER.indexOf(t);
    return (items || []).filter(isDocument).map((item, i) => ({ item, i })).sort((a, b) =>
        rank(a.item.item_type) - rank(b.item.item_type)
        || (Number(a.item.sort_order ?? a.i) - Number(b.item.sort_order ?? b.i))
        || (Number(a.item.id) - Number(b.item.id))).map(({ item }) => item);
}

/** Tab label: the short built-in name, or the title when it's a user document or there are several of a type. */
export function documentLabel(item, docs = []) {
    const builtIn = LABELS[item.item_type];
    const sameType = docs.filter((d) => d.item_type === item.item_type).length;
    if (!builtIn || sameType > 1) return String(item.title || builtIn || 'Document').trim() || 'Document';
    return builtIn;
}

/** The open document: the remembered one if it still exists, else FRAC, else the first. Always one when any exist. */
export function openDocument(docs, selectedId) {
    if (!docs.length) return null;
    return docs.find((d) => String(d.id) === String(selectedId ?? '')) || docs.find((d) => d.item_type === 'frac') || docs[0];
}

/** Roving tabindex movement for role=tab buttons. Returns the index to focus, or -1 for keys it ignores. */
export function moveTab(count, index, key) {
    if (!count) return -1;
    if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % count;
    if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + count) % count;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    return -1;
}

export const DOC_MODES = ['inline', 'raw'];
export const normaliseMode = (mode) => (DOC_MODES.includes(mode) ? mode : 'inline');
