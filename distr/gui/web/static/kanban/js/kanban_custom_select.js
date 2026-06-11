(function() {
    "use strict";

    var OPEN_SELECT = null;

    function stripLeadingBullet(text) {
        return String(text || "").replace(/^\u25CF\s*/, "").trim();
    }

    function closeOpen() {
        if (OPEN_SELECT) {
            OPEN_SELECT._closeMenu();
            OPEN_SELECT = null;
        }
    }

    document.addEventListener("click", function(e) {
        if (!e.target.closest(".kb-custom-select")) closeOpen();
    });
    document.addEventListener("keydown", function(e) {
        if (e.key === "Escape") closeOpen();
    });

    function upgrade(nativeSelect, opts) {
        opts = opts || {};
        if (!nativeSelect || nativeSelect.dataset.kbCustomSelect === "1") {
            return nativeSelect && nativeSelect._kbCustomSelect ? nativeSelect._kbCustomSelect : null;
        }

        nativeSelect.classList.add("kb-custom-select-native");
        var wrap = document.createElement("div");
        wrap.className = "kb-custom-select";
        nativeSelect.parentNode.insertBefore(wrap, nativeSelect);
        wrap.appendChild(nativeSelect);

        var trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "kb-custom-select-trigger";
        trigger.setAttribute("aria-haspopup", "listbox");
        trigger.setAttribute("aria-expanded", "false");

        var labelSpan = document.createElement("span");
        labelSpan.className = "kb-custom-select-label";

        var chevron = document.createElement("span");
        chevron.className = "kb-custom-select-chevron";
        chevron.setAttribute("aria-hidden", "true");
        chevron.textContent = "\u25BE";

        trigger.appendChild(labelSpan);
        trigger.appendChild(chevron);

        var menu = document.createElement("div");
        menu.className = "kb-custom-select-menu hidden";
        menu.setAttribute("role", "listbox");

        wrap.appendChild(trigger);
        wrap.appendChild(menu);

        var api = {
            _linkedValues: {},
            _closeMenu: function() {
                menu.classList.add("hidden");
                trigger.setAttribute("aria-expanded", "false");
                if (OPEN_SELECT === api) OPEN_SELECT = null;
            },
            _openMenu: function() {
                closeOpen();
                menu.classList.remove("hidden");
                trigger.setAttribute("aria-expanded", "true");
                OPEN_SELECT = api;
            },
            setLinkedValues: function(linkedMap) {
                api._linkedValues = linkedMap || {};
                buildMenu();
            },
            refresh: buildMenu,
        };

        function selectedLabel() {
            var opt = nativeSelect.options[nativeSelect.selectedIndex];
            if (!opt || !nativeSelect.value) return "";
            return stripLeadingBullet(opt.textContent);
        }

        function updateTriggerLabel() {
            var text = selectedLabel();
            labelSpan.textContent = text || (opts.placeholder || "Select...");
            trigger.classList.toggle("is-placeholder", !nativeSelect.value);
        }

        function buildMenu() {
            menu.innerHTML = "";
            var selected = String(nativeSelect.value || "");
            Array.prototype.forEach.call(nativeSelect.options, function(opt) {
                var value = String(opt.value || "");
                var item = document.createElement("button");
                item.type = "button";
                item.className = "kb-custom-select-option" + (value === selected ? " is-selected" : "");
                item.setAttribute("role", "option");
                item.setAttribute("aria-selected", value === selected ? "true" : "false");

                var text = document.createElement("span");
                text.className = "kb-custom-select-option-label";
                text.textContent = stripLeadingBullet(opt.textContent) || (opts.emptyLabel || "None");

                item.appendChild(text);

                if (api._linkedValues[value]) {
                    var dot = document.createElement("span");
                    dot.className = "kb-custom-select-linked-dot";
                    dot.setAttribute("aria-label", "Linked to this board");
                    item.appendChild(dot);
                }

                item.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    nativeSelect.value = value;
                    nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
                    buildMenu();
                    api._closeMenu();
                });
                menu.appendChild(item);
            });
            updateTriggerLabel();
        }

        trigger.addEventListener("click", function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (menu.classList.contains("hidden")) api._openMenu();
            else api._closeMenu();
        });

        nativeSelect.addEventListener("change", buildMenu);

        nativeSelect.dataset.kbCustomSelect = "1";
        nativeSelect._kbCustomSelect = api;
        buildMenu();
        return api;
    }

    function refresh(nativeSelect) {
        if (nativeSelect && nativeSelect._kbCustomSelect) nativeSelect._kbCustomSelect.refresh();
    }

    function upgradeById(selectId, opts) {
        var el = document.getElementById(selectId);
        return el ? upgrade(el, opts) : null;
    }

    window.KanbanCustomSelect = {
        upgrade: upgrade,
        upgradeById: upgradeById,
        refresh: refresh,
        refreshById: function(selectId) {
            var el = document.getElementById(selectId);
            if (el) refresh(el);
        },
        closeAll: closeOpen,
    };
})();
