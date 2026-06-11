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

        var swatchSpan = document.createElement("span");
        swatchSpan.className = "kb-custom-select-color-swatch is-empty";
        swatchSpan.setAttribute("aria-hidden", "true");

        var labelSpan = document.createElement("span");
        labelSpan.className = "kb-custom-select-label";

        var chevron = document.createElement("span");
        chevron.className = "kb-custom-select-chevron";
        chevron.setAttribute("aria-hidden", "true");
        chevron.textContent = "\u25BE";

        trigger.appendChild(swatchSpan);
        trigger.appendChild(labelSpan);
        trigger.appendChild(chevron);

        var menu = document.createElement("div");
        menu.className = "kb-custom-select-menu hidden";
        menu.setAttribute("role", "listbox");

        wrap.appendChild(trigger);
        wrap.appendChild(menu);

        var api = {
            _linkedValues: {},
            _colorValues: opts.colorValues || {},
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
            setColorValues: function(colorMap) {
                api._colorValues = colorMap || {};
                buildMenu();
            },
            refresh: buildMenu,
        };

        function normalizeColor(value) {
            var raw = String(value || "").trim();
            if (!raw) return "";
            if (!raw.startsWith("#")) raw = "#" + raw;
            if (!/^#[0-9a-fA-F]{3,8}$/.test(raw)) return "";
            if (raw.length === 4) {
                return "#" + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3];
            }
            return raw.slice(0, 7);
        }

        function applySwatch(el, value) {
            if (!el) return;
            var color = normalizeColor(api._colorValues[value]);
            if (color) {
                el.classList.remove("is-empty");
                el.style.backgroundColor = color;
            } else {
                el.classList.add("is-empty");
                el.style.backgroundColor = "#ffffff";
            }
        }

        function selectedLabel() {
            var opt = nativeSelect.options[nativeSelect.selectedIndex];
            if (!opt || !nativeSelect.value) return "";
            return stripLeadingBullet(opt.textContent);
        }

        function updateTriggerLabel() {
            var text = selectedLabel();
            labelSpan.textContent = text || (opts.placeholder || "Select...");
            trigger.classList.toggle("is-placeholder", !nativeSelect.value);
            applySwatch(swatchSpan, nativeSelect.value);
        }

        function appendMenuOption(opt, selectedValue) {
            var value = String(opt.value || "");
            if (!value) return;

            var item = document.createElement("button");
            item.type = "button";
            item.className = "kb-custom-select-option" + (value === selectedValue ? " is-selected" : "");
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", value === selectedValue ? "true" : "false");

            var text = document.createElement("span");
            text.className = "kb-custom-select-option-label";
            text.textContent = stripLeadingBullet(opt.textContent) || (opts.emptyLabel || "None");

            var swatch = document.createElement("span");
            swatch.className = "kb-custom-select-color-swatch";
            swatch.setAttribute("aria-hidden", "true");
            applySwatch(swatch, value);
            item.appendChild(swatch);
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
        }

        function buildMenu() {
            menu.innerHTML = "";
            var selected = String(nativeSelect.value || "");
            Array.prototype.forEach.call(nativeSelect.childNodes, function(node) {
                var tag = String(node.tagName || node.nodeName || "").toUpperCase();
                if (tag === "OPTGROUP") {
                    var heading = document.createElement("div");
                    heading.className = "kb-custom-select-group-label";
                    heading.textContent = node.label || "";
                    menu.appendChild(heading);
                    Array.prototype.forEach.call(node.children || [], function(opt) {
                        if (String(opt.tagName || opt.nodeName || "").toUpperCase() === "OPTION") {
                            appendMenuOption(opt, selected);
                        }
                    });
                    return;
                }
                if (tag === "OPTION") appendMenuOption(node, selected);
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
