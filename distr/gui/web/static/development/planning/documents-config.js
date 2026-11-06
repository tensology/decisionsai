// Documents section feature flags. User-added documents (plan_items with item_type "document")
// are approved (2026-11-02), so add / rename / delete is ON. Tests can force it either way with an init script that
// sets window.__DECISIONS_TEST_FLAGS__ = { userDocuments: true|false } before the app loads (nothing in the app sets it).
export const USER_DOCUMENTS_ENABLED = true;
export const USER_DOCUMENT_TYPE = 'document';
export function userDocumentsEnabled(scope = (typeof window !== 'undefined' ? window : {})) {
    const override = scope.__DECISIONS_TEST_FLAGS__?.userDocuments;
    return typeof override === 'boolean' ? override : USER_DOCUMENTS_ENABLED;
}
