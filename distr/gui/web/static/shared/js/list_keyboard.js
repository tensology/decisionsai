/**
 * Shared left-panel list keyboard behavior:
 * - Arrow keys move focus across rows (uses selected id when focus left the row)
 * - Enter selects / custom action
 * - Delete opens remove flow (document-level when an item is selected)
 * - Skips when typing or decisions-confirm-modal is open
 */
(function () {
    "use strict";

    var docBindings = {};

    function resolveListEl(listEl) {
        if (!listEl) return null;
        if (typeof listEl === "string") return document.getElementById(listEl);
        return listEl;
    }

    function isTypingTarget(target) {
        if (!target) return false;
        var tag = (target.tagName || "").toLowerCase();
        return !!(target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select");
    }

    function isConfirmOpen() {
        return !!document.getElementById("decisions-confirm-modal");
    }

    function isNavKey(key, axis) {
        if (axis === "horizontal") {
            return key === "ArrowLeft" || key === "ArrowRight" || key === "ArrowUp" || key === "ArrowDown";
        }
        return key === "ArrowDown" || key === "ArrowUp";
    }

    function offsetForKey(key, axis) {
        if (axis === "horizontal") {
            if (key === "ArrowRight" || key === "ArrowDown") return 1;
            return -1;
        }
        return key === "ArrowDown" ? 1 : -1;
    }

    function bind(options) {
        options = options || {};
        var listEl = resolveListEl(options.listEl);
        if (!listEl) return;

        var boundKey = options.boundKey || "listKeyboardBound";
        if (listEl.dataset[boundKey] === "1") return;
        listEl.dataset[boundKey] = "1";

        var rowSelector = options.rowSelector;
        var getSelectedId = options.getSelectedId || null;
        var getRowId = options.getRowId;
        var onSelect = options.onSelect || null;
        var onEnter = options.onEnter || null;
        var onDelete = options.onDelete || null;
        var axis = options.axis || "vertical";
        var pageGuard = options.pageGuard || null;
        var shouldSkip = options.shouldSkip || null;
        var selectOnNavigate = !!options.selectOnNavigate;
        var ignoreEnterFrom = options.ignoreEnterFrom || null;
        var allowBackspaceDelete = options.allowBackspaceDelete !== false;
        var namespace = options.namespace || (listEl.id || boundKey);

        function rows() {
            return Array.prototype.slice.call(listEl.querySelectorAll(rowSelector));
        }

        function rowFromTarget(target) {
            return target && target.closest ? target.closest(rowSelector) : null;
        }

        function resolveActiveIndex(target) {
            var listRows = rows();
            if (!listRows.length) return { listRows: listRows, idx: -1 };
            var active = rowFromTarget(target) || rowFromTarget(document.activeElement);
            var idx = listRows.indexOf(active);
            if (idx < 0 && getSelectedId) {
                var sid = getSelectedId();
                if (sid != null && sid !== "") {
                    idx = listRows.findIndex(function (row) {
                        return String(getRowId(row)) === String(sid);
                    });
                }
            }
            return { listRows: listRows, idx: idx };
        }

        function focusByOffset(offset, target) {
            var resolved = resolveActiveIndex(target);
            var listRows = resolved.listRows;
            var idx = resolved.idx;
            if (!listRows.length) return null;
            if (idx < 0) idx = offset > 0 ? -1 : 0;
            var next = listRows[Math.max(0, Math.min(listRows.length - 1, idx + offset))];
            if (next) next.focus();
            return next;
        }

        function resolveDeleteId(target) {
            var row = rowFromTarget(target);
            if (row) return getRowId(row);
            if (getSelectedId) return getSelectedId();
            return null;
        }

        function shouldSkipEvent(e, requireListFocusForNav) {
            if (pageGuard && !pageGuard()) return true;
            if (shouldSkip && shouldSkip(e)) return true;
            if (isConfirmOpen()) return true;
            if (isTypingTarget(e.target)) return true;
            if (requireListFocusForNav && !rowFromTarget(e.target) && !getSelectedId) return true;
            return false;
        }

        function handleKeydown(e, opts) {
            opts = opts || {};
            var allowDeleteWithoutRowFocus = !!opts.allowDeleteWithoutRowFocus;
            var requireListFocusForNav = !!opts.requireListFocusForNav;

            if (isNavKey(e.key, axis)) {
                if (shouldSkipEvent(e, requireListFocusForNav)) return;
                if (requireListFocusForNav && !listEl.contains(e.target) && !getSelectedId) return;
                e.preventDefault();
                var next = focusByOffset(offsetForKey(e.key, axis), e.target);
                if (next && selectOnNavigate && onSelect) {
                    onSelect(getRowId(next), next);
                }
                return;
            }

            if (e.key === "Enter") {
                if (shouldSkipEvent(e, true)) return;
                var enterRow = rowFromTarget(e.target);
                if (!enterRow) return;
                if (ignoreEnterFrom && e.target.closest && e.target.closest(ignoreEnterFrom)) return;
                e.preventDefault();
                if (onEnter) {
                    onEnter(getRowId(enterRow), enterRow, e);
                } else if (onSelect) {
                    onSelect(getRowId(enterRow), enterRow);
                }
                return;
            }

            var isDeleteKey = e.key === "Delete";
            var isBackspaceInList = allowBackspaceDelete && e.key === "Backspace" && e.target && listEl.contains(e.target);
            if (!isDeleteKey && !isBackspaceInList) return;
            if (!onDelete) return;
            if (shouldSkipEvent(e, false)) return;

            var id = resolveDeleteId(e.target);
            if ((id == null || id === "") && allowDeleteWithoutRowFocus && getSelectedId) {
                id = getSelectedId();
            }
            if (id == null || id === "") return;
            e.preventDefault();
            onDelete(id, e);
        }

        listEl.addEventListener("keydown", function (e) {
            handleKeydown(e, { requireListFocusForNav: true });
        });

        if (options.documentNavigate) {
            if (!docBindings[namespace + ":nav"]) {
                docBindings[namespace + ":nav"] = true;
                document.addEventListener("keydown", function (e) {
                    if (!isNavKey(e.key, axis)) return;
                    handleKeydown(e, { requireListFocusForNav: false });
                });
            }
        }

        if (options.documentDelete !== false && onDelete && getSelectedId) {
            if (!docBindings[namespace + ":delete"]) {
                docBindings[namespace + ":delete"] = true;
                document.addEventListener("keydown", function (e) {
                    if (e.key !== "Delete" && !(allowBackspaceDelete && e.key === "Backspace" && listEl.contains(e.target))) return;
                    handleKeydown(e, { allowDeleteWithoutRowFocus: true });
                });
            }
        }
    }

    window.DecisionsListKeyboard = {
        bind: bind,
        isTypingTarget: isTypingTarget,
        isConfirmOpen: isConfirmOpen,
    };
})();
