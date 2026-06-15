/**
 * Ticket board notes workspace in the main board area (tabs + note editor).
 */
(function () {
    "use strict";

    var documents = [];
    var currentDocId = null;
    var expanded = false;
    var saving = false;
    var ctxDocId = null;
    var renameModalDocId = null;
    var deps = {};

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    function docTabDeleteHtml() {
        return '<button type="button" class="kb-doc-tab-delete" data-action="delete" title="Delete note" aria-label="Delete note">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">'
            + '<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>'
            + "</svg></button>";
    }

    function docTabTitleHtml(title) {
        return '<span class="kb-doc-tab-title-wrap"><span class="kb-doc-tab-title">' + esc(title || "Untitled") + "</span></span>";
    }

    function getCurrentDoc() {
        if (!currentDocId) return null;
        return documents.find(function (d) { return d.id === currentDocId; }) || null;
    }

    function getDocById(id) {
        return documents.find(function (d) { return d.id === id; }) || null;
    }

    function readExpandedState() {
        try { return localStorage.getItem("kb_documents_expanded") === "1"; } catch (e) { return false; }
    }

    function writeExpandedState(value) {
        try { localStorage.setItem("kb_documents_expanded", value ? "1" : "0"); } catch (e) {}
    }

    function readLastSelectedId() {
        try { return localStorage.getItem("kb_documents_last_id") || ""; } catch (e) { return ""; }
    }

    function writeLastSelectedId(id) {
        try {
            if (id) localStorage.setItem("kb_documents_last_id", id);
            else localStorage.removeItem("kb_documents_last_id");
        } catch (e) {}
    }

    function isDirty() {
        var doc = getCurrentDoc();
        var textarea = document.getElementById("kb-doc-textarea");
        if (!doc || !textarea) return false;
        return (doc.content || "") !== textarea.value;
    }

    function isRenameModalOpen() {
        var modal = document.getElementById("kb-doc-rename-modal");
        return modal && !modal.classList.contains("hidden");
    }

    function flushPendingSave() {
        if (!isDirty()) return Promise.resolve();
        return saveCurrentNote({ silent: true });
    }

    function saveCurrentNote(opts) {
        opts = opts || {};
        var doc = getCurrentDoc();
        var textarea = document.getElementById("kb-doc-textarea");
        if (!doc || !textarea || !deps.apiFetch) return Promise.resolve();
        if (saving) return Promise.resolve();
        var content = textarea.value;
        if ((doc.content || "") === content) {
            if (!opts.silent && deps.showSnackbar) deps.showSnackbar("Note saved");
            return Promise.resolve();
        }
        saving = true;
        return deps.apiFetch("/api/tickets/documents/" + encodeURIComponent(doc.id), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: content }),
        }).then(function (updated) {
            if (updated.content != null) doc.content = updated.content;
            doc.modified_at = updated.modified_at || doc.modified_at;
            saving = false;
            if (!opts.silent && deps.showSnackbar) deps.showSnackbar("Note saved");
        }).catch(function (err) {
            saving = false;
            if (deps.showSnackbar) deps.showSnackbar((err && err.message) || "Could not save note", "error");
        });
    }

    function restoreMainPanel() {
        var docsMain = document.getElementById("kb-documents-main");
        if (docsMain) docsMain.classList.add("hidden");
        if (typeof deps.restoreMainPanel === "function") {
            deps.restoreMainPanel();
            return;
        }
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        if (boardView && !boardView.classList.contains("hidden")) return;
        if (emptyView) emptyView.classList.remove("hidden");
    }

    function showDocumentsMainPanel() {
        var docsMain = document.getElementById("kb-documents-main");
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        var loadingView = document.getElementById("kb-loading");
        var waThread = document.getElementById("kb-wa-thread-view");
        if (docsMain) docsMain.classList.remove("hidden");
        if (boardView) boardView.classList.add("hidden");
        if (emptyView) emptyView.classList.add("hidden");
        if (loadingView) loadingView.classList.add("hidden");
        if (waThread) waThread.classList.add("hidden");
    }

    function updateExpandedUi() {
        var todoBar = document.getElementById("kb-todo-bar");
        var chevron = document.getElementById("kb-todo-bar-chevron");

        if (expanded) showDocumentsMainPanel();
        else restoreMainPanel();

        if (todoBar) {
            todoBar.classList.toggle("active", expanded);
            todoBar.setAttribute("aria-expanded", expanded ? "true" : "false");
            todoBar.title = expanded ? "Back to board" : "Open notes";
        }
        if (chevron) chevron.style.transform = expanded ? "rotate(180deg)" : "";
        writeExpandedState(expanded);
    }

    function setExpanded(value) {
        expanded = !!value;
        updateExpandedUi();
        if (expanded) {
            loadDocuments().then(function () {
                renderDocumentViews();
            });
        } else {
            flushPendingSave();
            hideDocContextMenu();
            closeRenameModal();
        }
    }

    function toggleExpanded() {
        setExpanded(!expanded);
    }

    function collapseDocuments() {
        if (!expanded) return;
        setExpanded(false);
    }

    function renderDocumentMainTabs() {
        var list = document.getElementById("kb-doc-main-tabs");
        if (!list) return;
        if (!documents.length) {
            list.innerHTML = "";
            return;
        }
        list.innerHTML = documents.map(function (doc) {
            var active = doc.id === currentDocId;
            return '<button type="button" class="kb-doc-tab px-4 py-2 text-sm text-gray-400' + (active ? " active" : "") + '" data-id="' + esc(doc.id) + '" role="tab" aria-selected="' + (active ? "true" : "false") + '" tabindex="' + (active ? "0" : "-1") + '" title="' + esc(doc.title) + '">' + docTabTitleHtml(doc.title) + docTabDeleteHtml() + "</button>";
        }).join("");
        list.querySelectorAll("[data-id]").forEach(function (btn) {
            btn.addEventListener("click", function (evt) {
                if (evt.target.closest(".kb-doc-tab-delete")) return;
                selectDocument(btn.dataset.id);
            });
            var deleteBtn = btn.querySelector(".kb-doc-tab-delete");
            if (deleteBtn) {
                deleteBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    confirmDeleteDocument(btn.dataset.id);
                });
            }
            btn.addEventListener("contextmenu", function (evt) {
                openDocContextMenu(evt, btn.dataset.id);
            });
        });
        var activeTab = currentDocId ? list.querySelector('[data-id="' + CSS.escape(currentDocId) + '"]') : null;
        if (activeTab && typeof activeTab.scrollIntoView === "function") {
            activeTab.scrollIntoView({ block: "nearest", inline: "nearest" });
        }
    }

    function renderDocumentViews() {
        renderDocumentMainTabs();
        renderDocumentEditor();
    }

    function renderDocumentEditor() {
        var empty = document.getElementById("kb-doc-empty");
        var editor = document.getElementById("kb-doc-editor");
        var textarea = document.getElementById("kb-doc-textarea");
        var doc = getCurrentDoc();
        if (!documents.length || !doc) {
            if (empty) empty.classList.remove("hidden");
            if (editor) editor.classList.add("hidden");
            if (textarea) textarea.value = "";
            return;
        }
        if (empty) empty.classList.add("hidden");
        if (editor) editor.classList.remove("hidden");
        if (textarea && textarea.value !== (doc.content || "")) {
            textarea.value = doc.content || "";
        }
    }

    function selectDocument(id, opts) {
        opts = opts || {};
        if (id === currentDocId && !opts.force) return flushPendingSave();
        return flushPendingSave().then(function () {
            currentDocId = id;
            writeLastSelectedId(id);
            renderDocumentViews();
        });
    }

    function loadDocuments() {
        if (!deps.apiFetch) return Promise.resolve([]);
        return deps.apiFetch("/api/tickets/documents").then(function (data) {
            documents = Array.isArray(data) ? data : [];
            if (currentDocId && !documents.some(function (d) { return d.id === currentDocId; })) {
                currentDocId = null;
            }
            if (!currentDocId && documents.length) {
                var lastId = readLastSelectedId();
                currentDocId = (lastId && documents.some(function (d) { return d.id === lastId; })) ? lastId : documents[0].id;
            }
            return documents;
        }).catch(function (err) {
            if (deps.showSnackbar) deps.showSnackbar((err && err.message) || "Could not load notes", "error");
            documents = [];
            return [];
        });
    }

    function createDocument() {
        if (!deps.apiFetch) return;
        flushPendingSave();
        deps.apiFetch("/api/tickets/documents", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Untitled", content: "" }),
        }).then(function (doc) {
            documents.push(doc);
            currentDocId = doc.id;
            writeLastSelectedId(doc.id);
            if (!expanded) setExpanded(true);
            renderDocumentViews();
            var textarea = document.getElementById("kb-doc-textarea");
            if (textarea) textarea.focus();
            if (deps.showSnackbar) deps.showSnackbar("Note created");
        }).catch(function (err) {
            if (deps.showSnackbar) deps.showSnackbar((err && err.message) || "Could not create note", "error");
        });
    }

    function hideDocContextMenu() {
        var menu = document.getElementById("kb-doc-ctx-menu");
        if (menu) menu.classList.add("hidden");
        ctxDocId = null;
    }

    function openDocContextMenu(evt, docId) {
        evt.preventDefault();
        evt.stopPropagation();
        var menu = document.getElementById("kb-doc-ctx-menu");
        if (!menu) return;
        ctxDocId = docId;
        menu.classList.remove("hidden");
        menu.style.left = evt.clientX + "px";
        menu.style.top = evt.clientY + "px";
        var rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth - 8) menu.style.left = Math.max(8, window.innerWidth - rect.width - 8) + "px";
        if (rect.bottom > window.innerHeight - 8) menu.style.top = Math.max(8, window.innerHeight - rect.height - 8) + "px";
    }

    function openRenameModal(docId) {
        var doc = getDocById(docId);
        var modal = document.getElementById("kb-doc-rename-modal");
        var input = document.getElementById("kb-doc-rename-input");
        if (!doc || !modal || !input) return;
        renameModalDocId = docId;
        input.value = doc.title || "Untitled";
        modal.classList.remove("hidden");
        input.focus();
        input.select();
    }

    function closeRenameModal() {
        var modal = document.getElementById("kb-doc-rename-modal");
        if (modal) modal.classList.add("hidden");
        renameModalDocId = null;
    }

    function saveRenameFromModal() {
        var docId = renameModalDocId;
        var input = document.getElementById("kb-doc-rename-input");
        if (!docId || !input || !deps.apiFetch) return Promise.resolve();
        var doc = getDocById(docId);
        if (!doc) return Promise.resolve();
        var title = (input.value || "").trim() || "Untitled";
        if ((doc.title || "Untitled") === title) {
            closeRenameModal();
            return Promise.resolve();
        }
        return deps.apiFetch("/api/tickets/documents/" + encodeURIComponent(docId), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: title }),
        }).then(function (updated) {
            if (updated.title != null) doc.title = updated.title;
            doc.modified_at = updated.modified_at || doc.modified_at;
            closeRenameModal();
            renderDocumentMainTabs();
            if (deps.showSnackbar) deps.showSnackbar("Note renamed");
        }).catch(function (err) {
            if (deps.showSnackbar) deps.showSnackbar((err && err.message) || "Could not rename note", "error");
        });
    }

    function confirmDeleteDocument(docId) {
        var doc = documents.find(function (d) { return d.id === docId; });
        var title = (doc && doc.title) || "this note";
        if (!deps.showKanbanConfirm) return;
        deps.showKanbanConfirm({
            title: "Delete note",
            message: 'Delete note "' + title + '"? This cannot be undone.',
            confirmLabel: "Delete",
            danger: true,
            onConfirm: function () {
                if (deps.hideKanbanConfirm) deps.hideKanbanConfirm();
                deleteDocumentById(docId);
            },
        });
    }

    function deleteDocumentById(docId) {
        if (!docId || !deps.apiFetch) return;
        flushPendingSave().then(function () {
            return deps.apiFetch("/api/tickets/documents/" + encodeURIComponent(docId), { method: "DELETE" });
        }).then(function () {
            documents = documents.filter(function (d) { return d.id !== docId; });
            if (currentDocId === docId) {
                currentDocId = documents.length ? documents[0].id : null;
                writeLastSelectedId(currentDocId || "");
            }
            renderDocumentViews();
            if (deps.showSnackbar) deps.showSnackbar("Note deleted");
        }).catch(function (err) {
            if (deps.showSnackbar) deps.showSnackbar((err && err.message) || "Could not delete note", "error");
        });
    }

    function bindEvents() {
        var todoBar = document.getElementById("kb-todo-bar");
        if (todoBar) todoBar.addEventListener("click", toggleExpanded);

        var newBtn = document.getElementById("kb-doc-new-btn");
        if (newBtn) newBtn.addEventListener("click", function (evt) {
            evt.stopPropagation();
            if (!expanded) setExpanded(true);
            createDocument();
        });

        var emptyAdd = document.getElementById("kb-doc-empty-add");
        if (emptyAdd) emptyAdd.addEventListener("click", createDocument);

        var deleteBtn = document.getElementById("kb-doc-delete-btn");
        if (deleteBtn) deleteBtn.addEventListener("click", function () {
            if (currentDocId) confirmDeleteDocument(currentDocId);
        });

        var saveBtn = document.getElementById("kb-doc-save-btn");
        if (saveBtn) saveBtn.addEventListener("click", function () {
            saveCurrentNote();
        });

        var renameCancel = document.getElementById("kb-doc-rename-cancel");
        if (renameCancel) renameCancel.addEventListener("click", closeRenameModal);

        var renameSave = document.getElementById("kb-doc-rename-save");
        if (renameSave) renameSave.addEventListener("click", function () {
            saveRenameFromModal();
        });

        var renameModal = document.getElementById("kb-doc-rename-modal");
        if (renameModal) {
            renameModal.addEventListener("click", function (evt) {
                if (evt.target === renameModal) closeRenameModal();
            });
        }

        var renameInput = document.getElementById("kb-doc-rename-input");
        if (renameInput) {
            renameInput.addEventListener("keydown", function (evt) {
                if (evt.key === "Enter") {
                    evt.preventDefault();
                    saveRenameFromModal();
                }
                if (evt.key === "Escape") {
                    evt.preventDefault();
                    closeRenameModal();
                }
            });
        }

        var ctxMenu = document.getElementById("kb-doc-ctx-menu");
        if (ctxMenu) {
            var renameBtn = ctxMenu.querySelector(".kb-doc-ctx-rename");
            var deleteCtxBtn = ctxMenu.querySelector(".kb-doc-ctx-delete");
            if (renameBtn) renameBtn.addEventListener("click", function (evt) {
                evt.stopPropagation();
                var id = ctxDocId;
                hideDocContextMenu();
                if (!id) return;
                selectDocument(id, { force: true }).then(function () {
                    openRenameModal(id);
                });
            });
            if (deleteCtxBtn) deleteCtxBtn.addEventListener("click", function (evt) {
                evt.stopPropagation();
                var id = ctxDocId;
                hideDocContextMenu();
                if (id) confirmDeleteDocument(id);
            });
        }

        document.addEventListener("click", function (evt) {
            if (!evt.target.closest("#kb-doc-ctx-menu")) hideDocContextMenu();
        });

        document.addEventListener("keydown", function (evt) {
            if (!(evt.ctrlKey || evt.metaKey) || evt.key !== "s") return;
            if (isRenameModalOpen()) {
                evt.preventDefault();
                saveRenameFromModal();
                return;
            }
            if (!expanded || !currentDocId) return;
            evt.preventDefault();
            saveCurrentNote();
        });
    }

    function init(options) {
        deps = options || {};
        bindEvents();
        expanded = readExpandedState();
        updateExpandedUi();
        if (expanded) {
            loadDocuments().then(function () {
                renderDocumentViews();
            });
        }
    }

    function onSidebarTabChange(tab) {
        var todoBar = document.getElementById("kb-todo-bar");
        if (todoBar) todoBar.classList.toggle("hidden", tab === "messages");
        if (tab === "messages") setExpanded(false);
    }

    window.KanbanDocuments = {
        init: init,
        collapse: collapseDocuments,
        isExpanded: function () { return expanded; },
        flushSave: flushPendingSave,
        onSidebarTabChange: onSidebarTabChange,
    };
})();
