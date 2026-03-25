/**
 * Snippets page: load list from /api/snippets, edit/remove modal.
 * Only runs when #snippets-list exists. List shows description as title; trigger words as badges; edit/remove as icons.
 */
(function() {
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
        el.innerHTML = '<p class="text-sm text-gray-400">No snippets yet. Create snippets in the desktop app.</p>';
    }

    var editSvg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>';
    var removeSvg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';

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
                if (!data.length) {
                    setEmpty(el);
                    return;
                }
                el.innerHTML = data.map(function(s) {
                    var words = [];
                    try { words = JSON.parse(s.additional_trigger_words || '[]'); } catch (e) {}
                    var displayTitle = (s.description || '').trim() || (s.title || 'Untitled');
                    var triggerWords = (s.title ? [s.title] : []).concat(words).filter(Boolean);
                    var badgesHtml = triggerWords.map(function(w) {
                        return '<span class="trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm">' + escapeAttr(w) + '</span>';
                    }).join('');
                    return '<div class="border border-white/20 rounded-lg p-4 bg-[#152054] flex justify-between items-start gap-4">' +
                        '<div class="min-w-0 flex-1">' +
                        '<div class="font-medium text-white">' + escapeAttr(displayTitle) + '</div>' +
                        (s.description && (s.title || '').trim() ? '<p class="text-sm text-gray-400 mt-1">' + escapeAttr(s.title) + '</p>' : '') +
                        (badgesHtml ? '<div class="flex flex-wrap gap-2 mt-2">' + badgesHtml + '</div>' : '') +
                        '</div>' +
                        '<div class="flex gap-1 flex-shrink-0">' +
                        '<button type="button" class="snippet-edit p-1.5 rounded border border-white/20 text-gray-300 hover:bg-white/10 inline-flex" data-id="' + s.id + '" data-title="' + escapeAttr(s.title || '') + '" data-description="' + escapeAttr(s.description || '').replace(/\n/g, ' ') + '" data-triggers="' + escapeAttr(words.join(',')) + '" aria-label="Edit">' + editSvg + '</button>' +
                        '<button type="button" class="snippet-remove p-1.5 rounded border border-red-500/50 text-red-400 hover:bg-red-500/20 inline-flex" data-id="' + s.id + '" data-title="' + escapeAttr(displayTitle) + '" aria-label="Remove">' + removeSvg + '</button>' +
                        '</div></div>';
                }).join('');
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
                        if (!confirm('Remove snippet "' + title + '"? This cannot be undone.')) return;
                        fetch('/api/snippets/' + id, { method: 'DELETE' })
                            .then(function(r) {
                                if (r.ok) loadSnippets();
                                else r.json().then(function(e) { alert(e.detail || 'Failed to remove'); });
                            })
                            .catch(function() { alert('Failed to remove snippet'); });
                    });
                });
            })
            .catch(function() {
                if (el && el.innerHTML.indexOf('Loading') !== -1) setEmpty(el);
            });
    }

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
    snippetModal.addEventListener('click', function(e) { if (e.target === snippetModal) closeSnippetModal(); });
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
    var saveBtn = document.getElementById('snippet-modal-save');
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
    if (saveBtn) {
        saveBtn.addEventListener('click', function() {
            var id = document.getElementById('snippet-modal-id').value;
            var title = document.getElementById('snippet-modal-title').value.trim();
            var description = document.getElementById('snippet-modal-description').value.trim();
            var words = getSnippetModalTriggerWords();
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
            fetch('/api/snippets/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title, description: description, additional_trigger_words: JSON.stringify(words) })
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
