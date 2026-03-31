/**
 * Snippets page: load list from /api/snippets, edit/remove modal, search, pagination.
 */
(function() {
    var PAGE_SIZE = 20;
    var currentPage = 1;
    var searchText = "";
    var allSnippets = [];
    var pendingDeleteId = null;

    function escapeAttr(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function setError(el, msg) {
        el.innerHTML = '<p class="text-sm text-amber-400">' + msg + '</p>';
    }

    function setEmpty(el) {
        el.innerHTML = '<p class="text-sm text-gray-400">No snippets yet. Create one with + Add Snippet.</p>';
    }

    var mouthSvg = '<svg class="w-3.5 h-3.5 inline-block" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m-4 0h8m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"/></svg>';

    var editSvg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>';
    var removeSvg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';

    function filterSnippets(data) {
        if (!searchText.trim()) return data;
        var q = searchText.trim().toLowerCase();
        return data.filter(function(s) {
            var title = (s.title || "").toLowerCase();
            var desc = (s.description || "").toLowerCase();
            var triggers = (s.additional_trigger_words || "").toLowerCase();
            return title.indexOf(q) !== -1 || desc.indexOf(q) !== -1 || triggers.indexOf(q) !== -1;
        });
    }

    function renderPagination(filtered) {
        var paginationEl = document.getElementById('snippets-pagination');
        var pageInfo = document.getElementById('snippets-page-info');
        var prevBtn = document.getElementById('snippets-prev');
        var nextBtn = document.getElementById('snippets-next');
        if (!paginationEl) return;

        var totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        if (currentPage > totalPages) currentPage = totalPages;

        if (filtered.length <= PAGE_SIZE) {
            paginationEl.classList.add('hidden');
            return;
        }

        paginationEl.classList.remove('hidden');
        var start = (currentPage - 1) * PAGE_SIZE + 1;
        var end = Math.min(currentPage * PAGE_SIZE, filtered.length);
        pageInfo.textContent = start + '–' + end + ' of ' + filtered.length;
        prevBtn.disabled = currentPage <= 1;
        nextBtn.disabled = currentPage >= totalPages;
    }

    function getPageSlice(filtered) {
        var start = (currentPage - 1) * PAGE_SIZE;
        return filtered.slice(start, start + PAGE_SIZE);
    }

    function renderSnippets() {
        var el = document.getElementById('snippets-list');
        if (!el) return;
        var filtered = filterSnippets(allSnippets);
        renderPagination(filtered);
        var page = getPageSlice(filtered);

        if (!page.length) {
            if (allSnippets.length === 0) {
                setEmpty(el);
            } else {
                el.innerHTML = '<p class="text-sm text-gray-400">No snippets match your search.</p>';
            }
            return;
        }

        el.innerHTML = page.map(function(s) {
            var words = [];
            try { words = JSON.parse(s.additional_trigger_words || '[]'); } catch (e) {}
            var displayTitle = (s.title || '').trim() || 'Untitled';
            var triggerWords = (s.title ? [s.title] : []).concat(words).filter(Boolean);
            var badgesHtml = triggerWords.map(function(w) {
                return '<span class="trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm">' + escapeAttr(w) + '</span>';
            }).join('');
            var speakTooltip = 'Speak this: &quot;' + triggerWords.map(function(w) { return escapeAttr(w); }).join('&quot; or &quot;') + '&quot;';
            var speakIcon = triggerWords.length ? '<span class="inline-flex items-center text-gray-500 cursor-help relative group" title="' + speakTooltip + '">' + mouthSvg + '</span>' : '';
            return '<div class="border border-white/20 rounded-lg p-4 bg-[#152054] flex justify-between items-start gap-4">' +
                '<div class="min-w-0 flex-1">' +
                '<div class="font-medium text-white flex items-center gap-1.5"><span title="' + speakTooltip + '">' + escapeAttr(displayTitle) + '</span> ' + speakIcon + '</div>' +
                ((s.description || '').trim() ? '<p class="text-sm text-gray-400 mt-1">' + escapeAttr(s.description) + '</p>' : '') +
                (badgesHtml ? '<div class="flex flex-wrap gap-2 mt-2">' + badgesHtml + '</div>' : '') +
                '</div>' +
                '<div class="flex gap-1 flex-shrink-0">' +
                '<button type="button" class="snippet-edit p-1.5 rounded border border-white/20 text-gray-300 hover:bg-white/10 inline-flex" data-id="' + s.id + '" data-title="' + escapeAttr(s.title || '') + '" data-description="' + escapeAttr(s.description || '').replace(/\n/g, ' ') + '" data-triggers="' + escapeAttr(words.join(',')) + '" aria-label="Edit">' + editSvg + '</button>' +
                '<button type="button" class="snippet-remove p-1.5 rounded border border-red-500/50 text-red-400 hover:bg-red-500/20 inline-flex" data-id="' + s.id + '" data-title="' + escapeAttr(displayTitle) + '" aria-label="Remove">' + removeSvg + '</button>' +
                '</div></div>';
        }).join('');

        bindSnippetButtons();
    }

    function bindSnippetButtons() {
        document.querySelectorAll('.snippet-edit').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var id = btn.getAttribute('data-id');
                var title = (btn.getAttribute('data-title') || '').replace(/&quot;/g, '"');
                var description = (btn.getAttribute('data-description') || '').replace(/&quot;/g, '"');
                var triggersStr = (btn.getAttribute('data-triggers') || '').replace(/&quot;/g, '"');
                var words = triggersStr ? triggersStr.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];
                document.getElementById('snippet-modal-id').value = id;
                document.getElementById('snippet-modal-title').value = title;
                document.getElementById('snippet-modal-description').value = description;
                document.getElementById('snippet-modal-title-label').textContent = 'Edit snippet';
                renderSnippetModalTriggers(words);
                var inp = document.getElementById('snippet-modal-triggers-input');
                if (inp) inp.value = '';
                openSnippetModal();
                document.getElementById('snippet-modal-title').focus();
            });
        });
        document.querySelectorAll('.snippet-remove').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var id = btn.getAttribute('data-id');
                var title = (btn.getAttribute('data-title') || 'this snippet').replace(/&quot;/g, '"');
                openDeleteModal(id, title);
            });
        });
    }

    /* ---- Delete confirmation modal ---- */
    function openDeleteModal(id, name) {
        pendingDeleteId = id;
        var nameEl = document.getElementById('snippet-delete-name');
        if (nameEl) nameEl.textContent = name;
        var modal = document.getElementById('snippet-delete-modal');
        if (modal) { modal.classList.remove('hidden'); document.body.style.overflow = 'hidden'; }
    }

    function closeDeleteModal() {
        pendingDeleteId = null;
        var modal = document.getElementById('snippet-delete-modal');
        if (modal) { modal.classList.add('hidden'); document.body.style.overflow = ''; }
    }

    var deleteConfirmBtn = document.getElementById('snippet-delete-confirm');
    var deleteCancelBtn = document.getElementById('snippet-delete-cancel');
    var deleteModal = document.getElementById('snippet-delete-modal');

    if (deleteCancelBtn) deleteCancelBtn.addEventListener('click', closeDeleteModal);
    if (deleteModal) {
        deleteModal.addEventListener('click', function(e) {
            if (e.target === deleteModal) closeDeleteModal();
        });
    }
    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', function() {
            if (!pendingDeleteId) return;
            var id = pendingDeleteId;
            deleteConfirmBtn.disabled = true;
            deleteConfirmBtn.textContent = 'Removing…';
            fetch('/api/snippets/' + id, { method: 'DELETE' })
                .then(function(r) {
                    if (r.ok) { closeDeleteModal(); loadSnippets(); }
                    else r.json().then(function(e) { alert(e.detail || 'Failed to remove'); });
                })
                .catch(function() { alert('Failed to remove snippet'); })
                .finally(function() {
                    deleteConfirmBtn.disabled = false;
                    deleteConfirmBtn.textContent = 'Remove';
                });
        });
    }
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && deleteModal && !deleteModal.classList.contains('hidden')) closeDeleteModal();
    });

    /* ---- Add snippet ---- */
    var addBtn = document.getElementById('snippet-add-btn');
    if (addBtn) {
        addBtn.addEventListener('click', function() {
            document.getElementById('snippet-modal-id').value = '';
            document.getElementById('snippet-modal-title').value = '';
            document.getElementById('snippet-modal-description').value = '';
            document.getElementById('snippet-modal-title-label').textContent = 'Add snippet';
            renderSnippetModalTriggers([]);
            var inp = document.getElementById('snippet-modal-triggers-input');
            if (inp) inp.value = '';
            openSnippetModal();
            document.getElementById('snippet-modal-title').focus();
        });
    }

    /* ---- Load snippets ---- */
    function loadSnippets() {
        var el = document.getElementById('snippets-list');
        if (!el) return;
        fetch('/api/snippets')
            .then(function(r) {
                if (!r.ok) {
                    return r.json().then(function(err) {
                        setError(el, 'Could not load snippets: ' + (err && err.detail ? err.detail : 'HTTP ' + r.status));
                    }).catch(function() {
                        setError(el, 'Could not load snippets (HTTP ' + r.status + '). Check server logs.');
                    }).then(function() { throw new Error(r.status); });
                }
                return r.json();
            })
            .then(function(data) {
                if (!el) return;
                if (!Array.isArray(data)) data = [];
                allSnippets = data;
                renderSnippets();
            })
            .catch(function() {
                if (el && el.innerHTML.indexOf('Loading') !== -1) setEmpty(el);
            });
    }

    /* ---- Search ---- */
    var searchEl = document.getElementById('snippets-search');
    if (searchEl) {
        searchEl.addEventListener('input', function() {
            searchText = searchEl.value || '';
            currentPage = 1;
            renderSnippets();
        });
    }

    /* ---- Pagination ---- */
    var prevBtn = document.getElementById('snippets-prev');
    var nextBtn = document.getElementById('snippets-next');
    if (prevBtn) {
        prevBtn.addEventListener('click', function() {
            if (currentPage > 1) { currentPage--; renderSnippets(); }
        });
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', function() {
            var filtered = filterSnippets(allSnippets);
            var totalPages = Math.ceil(filtered.length / PAGE_SIZE);
            if (currentPage < totalPages) { currentPage++; renderSnippets(); }
        });
    }

    /* ---- Edit modal helpers ---- */
    function renderSnippetModalTriggers(words) {
        var wrap = document.getElementById('snippet-modal-triggers-wrap');
        if (!wrap) return;
        wrap.innerHTML = '';
        (words || []).forEach(function(w) {
            w = String(w).trim();
            if (!w) return;
            var badge = document.createElement('span');
            badge.className = 'trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm';
            badge.setAttribute('data-word', w);
            badge.innerHTML = '<span class="trigger-word">' + escapeAttr(w) + '</span> <button type="button" class="trigger-remove ml-0.5 text-gray-400 hover:text-white focus:outline-none" aria-label="Remove">&times;</button>';
            var removeBtn = badge.querySelector('.trigger-remove');
            if (removeBtn) removeBtn.addEventListener('click', function() { badge.remove(); });
            wrap.appendChild(badge);
        });
    }

    function getSnippetModalTriggerWords() {
        var wrap = document.getElementById('snippet-modal-triggers-wrap');
        if (!wrap) return [];
        var seen = {};
        var words = [];
        wrap.querySelectorAll('.trigger-badge[data-word]').forEach(function(b) {
            var w = (b.getAttribute('data-word') || '').trim();
            if (w && !seen[w]) { seen[w] = true; words.push(w); }
        });
        var input = document.getElementById('snippet-modal-triggers-input');
        if (input && (input.value || '').trim()) {
            (input.value || '').split(',').forEach(function(s) {
                var w = s.trim();
                if (w && !seen[w]) { seen[w] = true; words.push(w); }
            });
        }
        return words;
    }

    function addSnippetModalTriggerFromInput() {
        var input = document.getElementById('snippet-modal-triggers-input');
        if (!input) return;
        var raw = (input.value || '').trim();
        if (!raw) return;
        var toAdd = raw.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
        var wrap = document.getElementById('snippet-modal-triggers-wrap');
        if (!wrap) return;
        var existing = new Set();
        wrap.querySelectorAll('.trigger-badge[data-word]').forEach(function(b) { existing.add((b.getAttribute('data-word') || '').trim()); });
        toAdd.forEach(function(w) {
            if (existing.has(w)) return;
            existing.add(w);
            var badge = document.createElement('span');
            badge.className = 'trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm';
            badge.setAttribute('data-word', w);
            badge.innerHTML = '<span class="trigger-word">' + escapeAttr(w) + '</span> <button type="button" class="trigger-remove ml-0.5 text-gray-400 hover:text-white focus:outline-none" aria-label="Remove">&times;</button>';
            var removeBtn = badge.querySelector('.trigger-remove');
            if (removeBtn) removeBtn.addEventListener('click', function() { badge.remove(); });
            wrap.appendChild(badge);
        });
        input.value = '';
    }

    /* ---- Edit/Add modal ---- */
    var snippetModal = document.getElementById('snippet-modal');
    if (!snippetModal) return;

    function closeSnippetModal() {
        snippetModal.classList.add('hidden');
        document.body.style.overflow = '';
    }
    function openSnippetModal() {
        snippetModal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }
    snippetModal.addEventListener('click', function(e) {
        if (e.target === snippetModal) closeSnippetModal();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && !snippetModal.classList.contains('hidden')) closeSnippetModal();
    });
    var dialog = document.getElementById('snippet-modal-dialog');
    if (dialog) {
        dialog.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.id !== 'snippet-modal-triggers-input') { e.preventDefault(); document.getElementById('snippet-modal-save').click(); }
        });
    }
    var cancelBtn = document.getElementById('snippet-modal-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', closeSnippetModal);
    var triggersInput = document.getElementById('snippet-modal-triggers-input');
    if (triggersInput) {
        triggersInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault();
                addSnippetModalTriggerFromInput();
            }
        });
        triggersInput.addEventListener('blur', addSnippetModalTriggerFromInput);
    }

    var saveBtn = document.getElementById('snippet-modal-save');
    if (saveBtn) {
        saveBtn.addEventListener('click', function() {
            var id = document.getElementById('snippet-modal-id').value;
            var title = document.getElementById('snippet-modal-title').value.trim();
            var description = document.getElementById('snippet-modal-description').value.trim();
            var words = getSnippetModalTriggerWords();
            var isNew = !id;
            var url = isNew ? '/api/snippets' : '/api/snippets/' + id;
            var method = isNew ? 'POST' : 'PUT';

            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title || 'New Snippet', description: description, additional_trigger_words: JSON.stringify(words) })
            })
                .then(function(r) {
                    if (r.ok) { closeSnippetModal(); loadSnippets(); }
                    else r.json().then(function(e) { alert(e.detail || 'Failed to save'); });
                })
                .catch(function() { alert('Failed to save snippet'); })
                .finally(function() {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save';
                });
        });
    }

    if (document.getElementById('snippets-list')) loadSnippets();
})();
