(function () {
    "use strict";

    var TYPES = { date: true, time: true, "datetime-local": true };
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var active = null;
    var uid = 0;

    function pad(n) {
        return String(n).padStart(2, "0");
    }

    function isoDate(date) {
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
    }

    function parseDate(value) {
        var match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (!match) return null;
        var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function parseTime(value) {
        var match = String(value || "").match(/T?(\d{2}):(\d{2})/);
        return match ? { hour: Number(match[1]), minute: Number(match[2]) } : { hour: 9, minute: 0 };
    }

    function inputType(input) {
        return input.getAttribute("type") || "";
    }

    function labelFor(input) {
        if (input.getAttribute("data-decisions-datetime-label")) return input.getAttribute("data-decisions-datetime-label");
        if (input.id) {
            var label = document.querySelector('label[for="' + CSS.escape(input.id) + '"]');
            if (label) return label.textContent.trim();
        }
        return inputType(input) === "time" ? "Time" : "Date";
    }

    function formatValue(input) {
        var type = inputType(input);
        var value = input.value;
        if (!value) return "Select " + labelFor(input).toLowerCase();
        if (type === "time") return value;
        var date = parseDate(value);
        if (!date) return value;
        var dateText = date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
        if (type === "datetime-local") {
            return dateText + " at " + parseTime(value).hour.toString().padStart(2, "0") + ":" + parseTime(value).minute.toString().padStart(2, "0");
        }
        return dateText;
    }

    function iconFor(type) {
        if (type === "time") {
            return '<svg class="decisions-datetime-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6l4 2m4-2a8 8 0 11-16 0 8 8 0 0116 0z"/></svg>';
        }
        return '<svg class="decisions-datetime-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3M5 11h14M7 5h10a2 2 0 012 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2z"/></svg>';
    }

    function setInputValue(input, value) {
        input.value = value;
        refreshInput(input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function refreshInput(input) {
        var shell = input._decisionsDateTimeShell;
        if (!shell) return;
        var trigger = shell.querySelector(".decisions-datetime-trigger");
        var value = shell.querySelector(".decisions-datetime-value");
        if (value) value.textContent = formatValue(input);
        if (trigger) {
            trigger.disabled = input.disabled;
            trigger.setAttribute("aria-label", labelFor(input) + ": " + formatValue(input));
        }
    }

    function daysInView(viewDate) {
        var first = new Date(viewDate.getFullYear(), viewDate.getMonth(), 1);
        var start = new Date(first);
        start.setDate(start.getDate() - start.getDay());
        var days = [];
        for (var i = 0; i < 42; i += 1) {
            var day = new Date(start);
            day.setDate(start.getDate() + i);
            days.push(day);
        }
        return days;
    }

    function writeDate(input, date, state) {
        var type = inputType(input);
        if (type === "date") {
            setInputValue(input, isoDate(date));
            closePopover();
            return;
        }
        var time = parseTime(input.value);
        if (state) time = { hour: state.hour, minute: state.minute };
        setInputValue(input, isoDate(date) + "T" + pad(time.hour) + ":" + pad(time.minute));
    }

    function writeTime(input, hour, minute, state) {
        var type = inputType(input);
        if (state) {
            state.hour = hour;
            state.minute = minute;
        }
        if (type === "time") {
            setInputValue(input, pad(hour) + ":" + pad(minute));
            return;
        }
        var date = parseDate(input.value) || new Date();
        setInputValue(input, isoDate(date) + "T" + pad(hour) + ":" + pad(minute));
    }

    function buildCalendar(input, state) {
        var selected = parseDate(input.value);
        var todayIso = isoDate(new Date());
        var selectedIso = selected ? isoDate(selected) : "";
        var days = daysInView(state.viewDate);
        var html = '<div class="decisions-datetime-head">' +
            '<button type="button" class="decisions-datetime-nav" data-dtw-prev aria-label="Previous month">&#8249;</button>' +
            '<div class="decisions-datetime-month">' + MONTHS[state.viewDate.getMonth()] + " " + state.viewDate.getFullYear() + '</div>' +
            '<button type="button" class="decisions-datetime-nav" data-dtw-next aria-label="Next month">&#8250;</button>' +
        '</div><div class="decisions-datetime-weekdays">' +
            WEEKDAYS.map(function (day) { return '<div class="decisions-datetime-weekday">' + day + '</div>'; }).join("") +
        '</div><div class="decisions-datetime-grid">';
        html += days.map(function (day) {
            var value = isoDate(day);
            var cls = "decisions-datetime-day" +
                (day.getMonth() !== state.viewDate.getMonth() ? " is-muted" : "") +
                (value === todayIso ? " is-today" : "") +
                (value === selectedIso ? " is-selected" : "");
            return '<button type="button" class="' + cls + '" data-dtw-date="' + value + '">' + day.getDate() + '</button>';
        }).join("");
        return html + '</div>';
    }

    function timeColumn(kind, selected, max) {
        var html = '<div><div class="decisions-datetime-column-label">' + kind + '</div><div class="decisions-datetime-scroll">';
        for (var i = 0; i <= max; i += 1) {
            html += '<button type="button" class="decisions-datetime-choice' + (i === selected ? " is-selected" : "") + '" data-dtw-' + kind.toLowerCase() + '="' + i + '">' + pad(i) + '</button>';
        }
        return html + '</div></div>';
    }

    function buildTime(input, state) {
        var time = parseTime(input.value);
        state.hour = typeof state.hour === "number" ? state.hour : time.hour;
        state.minute = typeof state.minute === "number" ? state.minute : time.minute;
        return '<div class="decisions-datetime-time" data-time-only="' + (inputType(input) === "time" ? "true" : "false") + '">' +
            timeColumn("Hour", state.hour, 23) +
            timeColumn("Minute", state.minute, 59) +
        '</div>';
    }

    function renderPopoverBody(input, state) {
        var type = inputType(input);
        var calendarHtml = type !== "time" ? buildCalendar(input, state) : "";
        var timeHtml = type !== "date" ? buildTime(input, state) : "";
        if (type === "datetime-local") {
            return '<div class="decisions-datetime-body is-datetime">' +
                '<div class="decisions-datetime-calendar-pane">' + calendarHtml + "</div>" +
                '<div class="decisions-datetime-time-pane">' + timeHtml + "</div>" +
                "</div>";
        }
        return calendarHtml + timeHtml;
    }

    function renderPopover(input, popover, state) {
        var type = inputType(input);
        var selected = parseDate(input.value);
        state.viewDate = state.viewDate || selected || new Date();
        popover.classList.toggle("is-datetime", type === "datetime-local");
        popover.classList.toggle("is-time-only", type === "time");
        popover.innerHTML = renderPopoverBody(input, state) +
            '<div class="decisions-datetime-actions">' +
                '<button type="button" class="decisions-datetime-action" data-action="today">' + (type === "time" ? "Now" : "Today") + '</button>' +
                '<button type="button" class="decisions-datetime-action" data-action="clear">Clear</button>' +
                '<button type="button" class="decisions-datetime-action" data-action="done">Done</button>' +
            '</div>';

        popover.querySelectorAll("[data-dtw-date]").forEach(function (button) {
            button.addEventListener("click", function () {
                var date = parseDate(button.getAttribute("data-dtw-date"));
                if (!date) return;
                state.viewDate = date;
                writeDate(input, date, state);
                if (inputType(input) !== "date") renderPopover(input, popover, state);
            });
        });
        var prev = popover.querySelector("[data-dtw-prev]");
        var next = popover.querySelector("[data-dtw-next]");
        if (prev) prev.addEventListener("click", function () {
            state.viewDate = new Date(state.viewDate.getFullYear(), state.viewDate.getMonth() - 1, 1);
            renderPopover(input, popover, state);
        });
        if (next) next.addEventListener("click", function () {
            state.viewDate = new Date(state.viewDate.getFullYear(), state.viewDate.getMonth() + 1, 1);
            renderPopover(input, popover, state);
        });
        popover.querySelectorAll("[data-dtw-hour]").forEach(function (button) {
            button.addEventListener("click", function () {
                writeTime(input, Number(button.getAttribute("data-dtw-hour")), state.minute, state);
                renderPopover(input, popover, state);
            });
        });
        popover.querySelectorAll("[data-dtw-minute]").forEach(function (button) {
            button.addEventListener("click", function () {
                writeTime(input, state.hour, Number(button.getAttribute("data-dtw-minute")), state);
                renderPopover(input, popover, state);
            });
        });
        popover.querySelector('[data-action="today"]').addEventListener("click", function () {
            var now = new Date();
            state.viewDate = now;
            state.hour = now.getHours();
            state.minute = now.getMinutes();
            if (type === "date") writeDate(input, now, state);
            else if (type === "time") writeTime(input, state.hour, state.minute, state);
            else setInputValue(input, isoDate(now) + "T" + pad(state.hour) + ":" + pad(state.minute));
            renderPopover(input, popover, state);
        });
        popover.querySelector('[data-action="clear"]').addEventListener("click", function () {
            setInputValue(input, "");
            closePopover();
        });
        popover.querySelector('[data-action="done"]').addEventListener("click", closePopover);
        popover.querySelectorAll(".decisions-datetime-choice.is-selected").forEach(function (choice) {
            if (choice.scrollIntoView) choice.scrollIntoView({ block: "center" });
        });
        if (active && active.popover === popover && active.trigger) {
            positionPopover(active.trigger, popover);
        }
    }

    function positionPopover(trigger, popover) {
        popover.classList.add("is-portaled");
        if (popover.parentNode !== document.body) {
            document.body.appendChild(popover);
        }
        popover.style.left = "";
        popover.style.top = "";
        popover.style.right = "";
        popover.style.bottom = "";
        popover.removeAttribute("data-align");
        popover.removeAttribute("data-open");

        var triggerRect = trigger.getBoundingClientRect();
        var popoverRect = popover.getBoundingClientRect();
        var margin = 12;
        var gap = 8;
        var left = triggerRect.left;
        if (left + popoverRect.width > window.innerWidth - margin) {
            left = Math.max(margin, triggerRect.right - popoverRect.width);
            popover.setAttribute("data-align", "right");
        }
        var top = triggerRect.bottom + gap;
        if (top + popoverRect.height > window.innerHeight - margin) {
            top = Math.max(margin, triggerRect.top - popoverRect.height - gap);
            popover.setAttribute("data-open", "above");
        }
        popover.style.left = Math.round(left) + "px";
        popover.style.top = Math.round(top) + "px";
    }

    function bindPopoverReposition() {
        if (!active || active.repositionBound) return;
        active.repositionHandler = function () {
            if (!active) return;
            positionPopover(active.trigger, active.popover);
        };
        window.addEventListener("resize", active.repositionHandler);
        window.addEventListener("scroll", active.repositionHandler, true);
        active.repositionBound = true;
    }

    function unbindPopoverReposition() {
        if (!active || !active.repositionBound) return;
        window.removeEventListener("resize", active.repositionHandler);
        window.removeEventListener("scroll", active.repositionHandler, true);
        active.repositionBound = false;
        active.repositionHandler = null;
    }

    function closePopover() {
        if (!active) return;
        unbindPopoverReposition();
        active.trigger.setAttribute("aria-expanded", "false");
        active.popover.remove();
        active = null;
    }

    function openPopover(input) {
        if (input.disabled) return;
        var shell = input._decisionsDateTimeShell;
        var trigger = shell && shell.querySelector(".decisions-datetime-trigger");
        if (!shell || !trigger) return;
        if (active && active.input === input) {
            closePopover();
            return;
        }
        closePopover();
        refreshInput(input);
        var popover = document.createElement("div");
        var state = {};
        popover.className = "decisions-datetime-popover";
        popover.setAttribute("role", "dialog");
        popover.setAttribute("aria-label", labelFor(input));
        popover.addEventListener("click", function (event) {
            event.stopPropagation();
        });
        document.body.appendChild(popover);
        renderPopover(input, popover, state);
        positionPopover(trigger, popover);
        requestAnimationFrame(function () {
            if (!active || active.popover !== popover) return;
            positionPopover(trigger, popover);
        });
        bindPopoverReposition();
        trigger.setAttribute("aria-expanded", "true");
        active = { input: input, trigger: trigger, popover: popover, repositionBound: false };
    }

    function upgrade(input) {
        if (!input || input._decisionsDateTime || !TYPES[inputType(input)] || input.getAttribute("data-decisions-datetime") === "off") return;
        input._decisionsDateTime = true;
        var id = input.id || ("decisions-datetime-" + (++uid));
        input.id = id;
        var shell = document.createElement("span");
        var fullWidth = input.classList.contains("w-full") || input.classList.contains("form-control");
        shell.className = "decisions-datetime-shell " + (fullWidth ? "is-full" : "is-compact");
        var trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "decisions-datetime-trigger";
        trigger.setAttribute("data-decisions-datetime-trigger-for", id);
        trigger.setAttribute("aria-haspopup", "dialog");
        trigger.setAttribute("aria-expanded", "false");
        trigger.innerHTML = '<span class="decisions-datetime-value"></span>' + iconFor(inputType(input));

        input.parentNode.insertBefore(shell, input);
        shell.appendChild(input);
        shell.appendChild(trigger);
        input.classList.add("decisions-datetime-native");
        input._decisionsDateTimeShell = shell;

        trigger.addEventListener("click", function () { openPopover(input); });
        input.addEventListener("input", function () { refreshInput(input); });
        input.addEventListener("change", function () { refreshInput(input); });
        refreshInput(input);
    }

    function init(root) {
        Array.prototype.slice.call((root || document).querySelectorAll('input[type="date"], input[type="time"], input[type="datetime-local"]')).forEach(upgrade);
    }

    document.addEventListener("click", function (event) {
        if (!active) return;
        if (active.popover.contains(event.target) || active.trigger.contains(event.target)) return;
        closePopover();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closePopover();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { init(document); });
    } else {
        init(document);
    }

    new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            if (mutation.type === "attributes" && mutation.target && mutation.target._decisionsDateTime) {
                refreshInput(mutation.target);
                return;
            }
            mutation.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.matches && node.matches('input[type="date"], input[type="time"], input[type="datetime-local"]')) upgrade(node);
                init(node);
            });
        });
    }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "value"] });

    window.DecisionsDateTime = {
        init: init,
        refresh: function () { init(document); Array.prototype.slice.call(document.querySelectorAll(".decisions-datetime-native")).forEach(refreshInput); },
        refreshInput: refreshInput,
        close: closePopover
    };
})();
