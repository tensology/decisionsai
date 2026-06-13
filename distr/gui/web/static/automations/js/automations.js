/**
 * Automations page: left list always visible; right panel is editor or schedule calendar.
 */
(function () {
    "use strict";

    var currentAutomationId = null;
    var automationsData = [];
    var isCreating = false;
    var mainView = "automations";
    var calendarMode = "month";
    var calendarAnchor = null;
    var WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var WEEK_SLOT_MINUTES = 15;
    var WEEK_DAY_START_MINUTES = 0;
    var WEEK_DAY_END_MINUTES = 24 * 60;
    var CALENDAR_GRID_BASE_ROW_HEIGHT = 10;
    var CALENDAR_GRID_ZOOM = 1;
    var CALENDAR_GRID_ZOOM_MIN = 0.5;
    var CALENDAR_GRID_ZOOM_MAX = 2;
    var CALENDAR_GRID_ZOOM_STEP = 0.1;
    var CALENDAR_GRID_ZOOM_STORAGE_KEY = "automation-calendar-grid-zoom";

    function loadCalendarGridZoom() {
        try {
            var saved = parseFloat(localStorage.getItem(CALENDAR_GRID_ZOOM_STORAGE_KEY));
            if (!isNaN(saved) && saved >= CALENDAR_GRID_ZOOM_MIN && saved <= CALENDAR_GRID_ZOOM_MAX) {
                CALENDAR_GRID_ZOOM = saved;
            }
        } catch (e) {}
    }

    function saveCalendarGridZoom() {
        try {
            localStorage.setItem(CALENDAR_GRID_ZOOM_STORAGE_KEY, String(CALENDAR_GRID_ZOOM));
        } catch (e) {}
    }

    function calendarRowHeight() {
        return Math.max(4, Math.round(CALENDAR_GRID_BASE_ROW_HEIGHT * CALENDAR_GRID_ZOOM));
    }

    function calendarZoomPercent() {
        return Math.round(CALENDAR_GRID_ZOOM * 100);
    }

    function isCalendarShowingToday() {
        var today = startOfDay(new Date());
        if (calendarMode === "day") {
            return startOfDay(calendarAnchor).getTime() === today.getTime();
        }
        if (calendarMode === "week") {
            return startOfWeek(calendarAnchor).getTime() === startOfWeek(today).getTime();
        }
        return startOfMonth(calendarAnchor).getTime() === startOfMonth(today).getTime();
    }

    function updateCalendarTodayUi() {
        var btn = document.getElementById("automation-cal-today");
        if (!btn) return;
        btn.classList.toggle("hidden", isCalendarShowingToday());
    }

    function goToCalendarToday() {
        var today = startOfDay(new Date());
        if (calendarMode === "day") {
            calendarAnchor = today;
        } else if (calendarMode === "week") {
            calendarAnchor = startOfWeek(today);
        } else {
            calendarAnchor = startOfMonth(today);
        }
        renderCalendar();
    }

    function updateCalendarZoomUi() {
        var wrap = document.getElementById("automation-cal-view-tools");
        var tools = document.getElementById("automation-cal-zoom-tools");
        var label = document.getElementById("automation-cal-zoom-label");
        var todayBtn = document.getElementById("automation-cal-today");
        var exportBtn = document.getElementById("automation-cal-export");
        var isTimeGrid = calendarMode === "week" || calendarMode === "day";
        if (tools) tools.classList.toggle("hidden", !isTimeGrid);
        if (label) label.textContent = calendarZoomPercent() + "%";
        updateCalendarTodayUi();
        if (wrap) {
            var showToday = todayBtn && !todayBtn.classList.contains("hidden");
            var showZoom = tools && !tools.classList.contains("hidden");
            var showExport = !!exportBtn;
            wrap.classList.toggle("hidden", !showToday && !showZoom && !showExport);
        }
    }

    function formatExportPeriodLabel(startDate, endDate) {
        if (!startDate || !endDate) return "";
        if (startDate === endDate) return startDate;
        return startDate + " to " + endDate;
    }

    function getCalendarExportRange() {
        if (window.AutomationCalendarBlocks && window.AutomationCalendarBlocks.getVisibleExportRange) {
            var range = window.AutomationCalendarBlocks.getVisibleExportRange();
            if (range && range.start_date && range.end_date) return range;
        }
        var anchor = startOfDay(calendarAnchor);
        var start = anchor;
        var end = anchor;
        if (calendarMode === "week") {
            start = startOfWeek(anchor);
            end = addDays(start, 6);
        } else if (calendarMode === "month") {
            start = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
            end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
        }
        function toKey(d) {
            var y = d.getFullYear();
            var m = String(d.getMonth() + 1).padStart(2, "0");
            var day = String(d.getDate()).padStart(2, "0");
            return y + "-" + m + "-" + day;
        }
        return { start_date: toKey(start), end_date: toKey(end) };
    }

    function closeExportModal() {
        var modal = document.getElementById("sched-export-modal");
        if (modal) modal.classList.add("hidden");
    }

    function renderExportBoardList(boards) {
        var list = document.getElementById("sched-export-board-list");
        var empty = document.getElementById("sched-export-empty");
        var downloadBtn = document.getElementById("sched-export-download");
        if (!list) return;
        if (!boards || !boards.length) {
            list.innerHTML = "";
            if (empty) empty.classList.remove("hidden");
            if (downloadBtn) downloadBtn.disabled = true;
            return;
        }
        if (empty) empty.classList.add("hidden");
        if (downloadBtn) downloadBtn.disabled = false;
        list.innerHTML = boards.map(function(board) {
            var count = board.block_count ? " (" + board.block_count + ")" : "";
            return (
                '<label class="sched-export-board-item">' +
                '<input type="checkbox" class="sched-export-board-check" value="' + escapeAttr(board.board_key) + '" checked>' +
                "<span>" + escapeAttr(board.board_name || "No board") + count + "</span>" +
                "</label>"
            );
        }).join("");
    }

    function setExportBoardChecks(checked) {
        document.querySelectorAll(".sched-export-board-check").forEach(function(input) {
            input.checked = !!checked;
        });
    }

    function selectedExportBoardKeys() {
        return Array.prototype.slice.call(document.querySelectorAll(".sched-export-board-check:checked"))
            .map(function(input) { return input.value; })
            .filter(Boolean);
    }

    function openExportModal() {
        var modal = document.getElementById("sched-export-modal");
        var period = document.getElementById("sched-export-period");
        if (!modal) return;
        var range = getCalendarExportRange();
        if (period) {
            period.textContent = "Export time blocks for " + formatExportPeriodLabel(range.start_date, range.end_date);
        }
        modal.classList.remove("hidden");
        renderExportBoardList([]);
        var downloadBtn = document.getElementById("sched-export-download");
        if (downloadBtn) downloadBtn.disabled = true;
        apiFetch(
            "/api/schedule-blocks/export/boards?start_date=" + encodeURIComponent(range.start_date) +
            "&end_date=" + encodeURIComponent(range.end_date)
        ).then(function(data) {
            renderExportBoardList((data && data.boards) || []);
        }).catch(function(err) {
            showSnackbar((err && err.message) || "Could not load boards for export.", "error");
            closeExportModal();
        });
    }

    function downloadTimesheetExport() {
        var range = getCalendarExportRange();
        var boardKeys = selectedExportBoardKeys();
        if (!boardKeys.length) {
            showSnackbar("Select at least one board.", "warning");
            return;
        }
        var downloadBtn = document.getElementById("sched-export-download");
        if (downloadBtn) downloadBtn.disabled = true;
        fetch("/api/schedule-blocks/export/timesheet", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                start_date: range.start_date,
                end_date: range.end_date,
                board_keys: boardKeys,
            }),
        }).then(function(res) {
            if (!res.ok) {
                return res.json().catch(function() { return {}; }).then(function(body) {
                    throw new Error((body && body.detail) || res.statusText || "Export failed");
                });
            }
            var disposition = res.headers.get("Content-Disposition") || "";
            var match = disposition.match(/filename=\"?([^\";]+)\"?/i);
            var filename = (match && match[1]) || ("timesheet_" + range.start_date + "_to_" + range.end_date + ".xlsx");
            return res.blob().then(function(blob) {
                return { blob: blob, filename: filename };
            });
        }).then(function(result) {
            var url = URL.createObjectURL(result.blob);
            var link = document.createElement("a");
            link.href = url;
            link.download = result.filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            showSnackbar("Timesheet downloaded.", "success");
            closeExportModal();
        }).catch(function(err) {
            showSnackbar((err && err.message) || "Could not export timesheet.", "error");
        }).finally(function() {
            if (downloadBtn) downloadBtn.disabled = false;
        });
    }

    function setCalendarGridZoom(nextZoom) {
        CALENDAR_GRID_ZOOM = Math.max(
            CALENDAR_GRID_ZOOM_MIN,
            Math.min(CALENDAR_GRID_ZOOM_MAX, Math.round(nextZoom * 10) / 10)
        );
        saveCalendarGridZoom();
        updateCalendarZoomUi();
        if (mainView === "calendar" && (calendarMode === "week" || calendarMode === "day")) {
            renderCalendar();
        }
    }

    var apiFetch = window.DecisionsAPI ? window.DecisionsAPI.fetch : function(path, options) {
        return fetch(path, options).then(function(res) {
            if (!res.ok) throw new Error(res.statusText || "Request failed");
            return res.json();
        });
    };

    function showSnackbar(msg, type) {
        if (window.DecisionsAPI && window.DecisionsAPI.snackbar) {
            window.DecisionsAPI.snackbar(msg, type || "info", { id: "automation-snackbar" });
        }
    }

    function automationKeyboardTargetIsEditable(target) {
        if (!target) return false;
        var tagName = String(target.tagName || "").toLowerCase();
        return tagName === "input" ||
            tagName === "textarea" ||
            tagName === "select" ||
            target.isContentEditable;
    }

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function shortDate(value) {
        if (!value) return "-";
        try {
            return new Date(value).toLocaleString();
        } catch (_) {
            return value;
        }
    }

    function normalizeScheduleKind(kind) {
        kind = String(kind || "daily").toLowerCase();
        if (kind === "15min" || kind === "15m") return "interval";
        if (kind === "30min" || kind === "30m") return "interval";
        return kind;
    }

    function scheduleLabel(schedule) {
        var cfg = schedule || {};
        var kind = normalizeScheduleKind(cfg.kind || cfg.frequency || "daily");
        var labels = {
            once: "Once",
            interval: "Every " + (cfg.interval || 15) + " " + (cfg.interval_unit === "seconds" ? "seconds" : "minutes"),
            hourly: "Hourly",
            daily: "Daily",
            weekly: "Weekly",
            monthly: "Monthly"
        };
        if (kind === "once") return cfg.run_at ? "Once at " + shortDate(cfg.run_at) : "Once";
        if (kind === "interval") {
            if (cfg.kind === "15min" || cfg.kind === "15m") return "Every 15 minutes";
            if (cfg.kind === "30min" || cfg.kind === "30m") return "Every 30 minutes";
            return labels.interval;
        }
        if (cfg.time && (kind === "daily" || kind === "weekly" || kind === "monthly")) {
            var suffix = (labels[kind] || kind) + " at " + cfg.time;
            if (kind === "weekly" && cfg.days) suffix += " on " + formatWeeklyDaysLabel(cfg.days);
            if (kind === "monthly" && cfg.days) suffix += " on day " + cfg.days;
            return suffix;
        }
        return labels[kind] || kind;
    }

    var WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

    function formatWeeklyDaysLabel(raw) {
        var days = String(raw || "").split(",").map(function(part) {
            return parseInt(part.trim(), 10);
        }).filter(function(n) { return !isNaN(n) && n >= 0 && n <= 6; });
        if (!days.length) return "Mon";
        return days.map(function(n) { return WEEKDAY_LABELS[n] || String(n); }).join(", ");
    }

    function renderMonthlyDayBadges(days) {
        var wrap = document.getElementById("automation-monthly-badges");
        if (!wrap) return;
        wrap.innerHTML = "";
        (days || []).forEach(function(day) {
            var value = String(day).trim();
            if (!value) return;
            var badge = document.createElement("span");
            badge.className = "automation-monthly-day-badge";
            badge.setAttribute("data-day", value);
            badge.innerHTML = '<span class="automation-monthly-day-text">' + escapeAttr(value) + '</span>' +
                ' <button type="button" class="automation-monthly-day-remove text-gray-400 hover:text-white focus:outline-none" aria-label="Remove">&times;</button>';
            var removeBtn = badge.querySelector(".automation-monthly-day-remove");
            if (removeBtn) {
                removeBtn.addEventListener("click", function() { badge.remove(); });
            }
            wrap.appendChild(badge);
        });
    }

    function getMonthlyDaysArray() {
        var wrap = document.getElementById("automation-monthly-badges");
        var input = document.getElementById("automation-monthly-days-input");
        var seen = {};
        var days = [];
        if (wrap) {
            wrap.querySelectorAll(".automation-monthly-day-badge[data-day]").forEach(function(badge) {
                var value = String(badge.getAttribute("data-day") || "").trim();
                if (!value || seen[value]) return;
                seen[value] = true;
                days.push(value);
            });
        }
        if (input && String(input.value || "").trim()) {
            String(input.value || "").split(",").forEach(function(part) {
                var value = part.trim();
                if (!value || seen[value]) return;
                seen[value] = true;
                days.push(value);
            });
        }
        return days;
    }

    function addMonthlyDaysFromInput() {
        var input = document.getElementById("automation-monthly-days-input");
        if (!input) return;
        var raw = String(input.value || "").trim();
        if (!raw) return;
        var existing = getMonthlyDaysArray();
        var seen = {};
        existing.forEach(function(day) { seen[day] = true; });
        raw.split(",").map(function(part) { return part.trim(); }).filter(Boolean).forEach(function(day) {
            if (seen[day]) return;
            seen[day] = true;
            existing.push(day);
        });
        input.value = "";
        renderMonthlyDayBadges(existing);
    }

    function setWeeklyDaySelection(raw) {
        var selected = String(raw || "1").split(",").map(function(part) {
            return parseInt(part.trim(), 10);
        }).filter(function(n) { return !isNaN(n); });
        if (!selected.length) selected = [1];
        document.querySelectorAll(".automation-weekday-btn").forEach(function(btn) {
            var day = parseInt(btn.getAttribute("data-day"), 10);
            btn.classList.toggle("is-selected", selected.indexOf(day) !== -1);
        });
    }

    function getWeeklyDaysCsv() {
        var days = [];
        document.querySelectorAll(".automation-weekday-btn.is-selected").forEach(function(btn) {
            days.push(btn.getAttribute("data-day"));
        });
        return days.length ? days.join(",") : "1";
    }

    function monthlyDaysMatch(dayDate, schedule) {
        var raw = String((schedule && schedule.days) || "1");
        var days = raw.split(",").map(function(part) {
            return parseInt(part.trim(), 10);
        }).filter(function(n) { return !isNaN(n) && n >= 1 && n <= 31; });
        if (!days.length) days = [1];
        return days.indexOf(dayDate.getDate()) !== -1;
    }

    function emptyAutomation() {
        return {
            id: "",
            name: "",
            status: "active",
            instruction: "",
            schedule: { kind: "daily", time: "09:00" },
            next_run_at: null
        };
    }

    function startOfDay(value) {
        var d = value instanceof Date ? new Date(value.getTime()) : new Date(value);
        d.setHours(0, 0, 0, 0);
        return d;
    }

    function startOfMonth(value) {
        var d = startOfDay(value);
        d.setDate(1);
        return d;
    }

    function startOfWeek(value) {
        var d = startOfDay(value);
        d.setDate(d.getDate() - d.getDay());
        return d;
    }

    function endOfWeek(value) {
        return addDays(startOfWeek(value), 6);
    }

    function monthGridDayCount(monthStart, gridStart) {
        var month = monthStart.getMonth();
        var monthEnd = startOfDay(new Date(monthStart.getFullYear(), month + 1, 0));
        var lastCell = endOfWeek(monthEnd);
        var totalDays = Math.round((lastCell.getTime() - gridStart.getTime()) / 86400000) + 1;
        while (totalDays > 7) {
            var rowAllOutside = true;
            for (var i = totalDays - 7; i < totalDays; i += 1) {
                if (addDays(gridStart, i).getMonth() === month) {
                    rowAllOutside = false;
                    break;
                }
            }
            if (!rowAllOutside) break;
            totalDays -= 7;
        }
        return totalDays;
    }

    function addDays(value, days) {
        var d = startOfDay(value);
        d.setDate(d.getDate() + days);
        return d;
    }

    function sameDay(a, b) {
        return startOfDay(a).getTime() === startOfDay(b).getTime();
    }

    function toDateKey(date) {
        var year = date.getFullYear();
        var month = String(date.getMonth() + 1).padStart(2, "0");
        var day = String(date.getDate()).padStart(2, "0");
        return year + "-" + month + "-" + day;
    }

    function isWeekendDay(dayDate) {
        var dow = dayDate.getDay();
        return dow === 0 || dow === 6;
    }

    function calculateEasterSunday(year) {
        var a = year % 19;
        var b = Math.floor(year / 100);
        var c = year % 100;
        var d = Math.floor(b / 4);
        var e = b % 4;
        var f = Math.floor((b + 8) / 25);
        var g = Math.floor((b - f + 1) / 3);
        var h = (19 * a + b - d - g + 15) % 30;
        var i = Math.floor(c / 4);
        var k = c % 4;
        var l = (32 + 2 * e + 2 * i - h - k) % 7;
        var m = Math.floor((a + 11 * h + 22 * l) / 451);
        var month = Math.floor((h + l - 7 * m + 114) / 31);
        var day = ((h + l - 7 * m + 114) % 31) + 1;
        return new Date(year, month - 1, day);
    }

    function buildSouthAfricanHolidayMap(yearStart, yearEnd) {
        var holidays = {};
        function addHoliday(date, holidayName, observedOnMondayIfSunday) {
            if (observedOnMondayIfSunday === undefined) observedOnMondayIfSunday = true;
            holidays[toDateKey(date)] = holidayName;
            if (observedOnMondayIfSunday && date.getDay() === 0) {
                var observed = new Date(date);
                observed.setDate(observed.getDate() + 1);
                holidays[toDateKey(observed)] = holidayName + " (Observed)";
            }
        }
        for (var year = yearStart; year <= yearEnd; year += 1) {
            addHoliday(new Date(year, 0, 1), "New Year's Day");
            addHoliday(new Date(year, 2, 21), "Human Rights Day");
            addHoliday(new Date(year, 3, 27), "Freedom Day");
            addHoliday(new Date(year, 4, 1), "Workers' Day");
            addHoliday(new Date(year, 5, 16), "Youth Day");
            addHoliday(new Date(year, 7, 9), "National Women's Day");
            addHoliday(new Date(year, 8, 24), "Heritage Day");
            addHoliday(new Date(year, 11, 16), "Day of Reconciliation");
            addHoliday(new Date(year, 11, 25), "Christmas Day");
            addHoliday(new Date(year, 11, 26), "Day of Goodwill");
            var easterSunday = calculateEasterSunday(year);
            var goodFriday = new Date(easterSunday);
            goodFriday.setDate(goodFriday.getDate() - 2);
            addHoliday(goodFriday, "Good Friday", false);
            var familyDay = new Date(easterSunday);
            familyDay.setDate(familyDay.getDate() + 1);
            addHoliday(familyDay, "Family Day", false);
        }
        return holidays;
    }

    function getSouthAfricanHolidayName(dayDate, holidayMap) {
        return holidayMap[toDateKey(dayDate)] || "";
    }

    function dayCalendarFlags(dayDate, today, holidayMap) {
        var classes = [];
        var holiday = getSouthAfricanHolidayName(dayDate, holidayMap);
        if (isWeekendDay(dayDate)) classes.push("is-weekend");
        if (holiday) classes.push("is-holiday");
        if (sameDay(dayDate, today)) classes.push("is-today");
        return { className: classes.join(" "), holiday: holiday };
    }

    function holidayMapForRange(startDate, dayCount) {
        var endDate = addDays(startDate, Math.max(0, dayCount - 1));
        return buildSouthAfricanHolidayMap(startDate.getFullYear(), endDate.getFullYear());
    }

    function formatTimeShort(value) {
        try {
            return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        } catch (_) {
            return "";
        }
    }

    function formatHourLabel(totalMinutes) {
        var hours = Math.floor(totalMinutes / 60);
        return String(hours).padStart(2, "0") + ":00";
    }

    function minutesFromDate(value) {
        return (value.getHours() * 60) + value.getMinutes();
    }

    function weekSlotStarts() {
        var slots = [];
        for (var m = WEEK_DAY_START_MINUTES; m < WEEK_DAY_END_MINUTES; m += WEEK_SLOT_MINUTES) {
            slots.push(m);
        }
        return slots;
    }

    function weekTimedEventsForDay(dayDate) {
        var timed = [];
        calendarEventsForDay(dayDate).forEach(function(evt) {
            if (evt.compact && evt.label === "Hourly") {
                for (var h = 0; h < 24; h += 1) {
                    var at = new Date(dayDate);
                    at.setHours(h, 0, 0, 0);
                    timed.push({
                        automation: evt.automation,
                        at: at,
                        label: "Hourly",
                        compact: true
                    });
                }
                return;
            }
            timed.push(evt);
        });
        return timed;
    }

    function bindWeekCalendarClicks(root) {
        if (!root) return;
        root.querySelectorAll(".automation-cal-week-block").forEach(function(btn) {
            btn.addEventListener("click", function() {
                openAutomationFromCalendar(btn.getAttribute("data-id"));
            });
        });
    }

    function scrollTimeGridCalendarToNow(root, periodStart, dayCount) {
        if (!root) return;
        var now = new Date();
        var periodEnd = addDays(periodStart, dayCount);
        if (now < periodStart || now >= periodEnd) return;
        var nowMinutes = minutesFromDate(now);
        var top = ((nowMinutes - WEEK_DAY_START_MINUTES) / WEEK_SLOT_MINUTES) * calendarRowHeight();
        root.scrollTop = Math.max(0, top - (root.clientHeight / 2));
    }

    function renderTimeGridDayColumn(dayDate, slotStarts, gridHeight, today, holidayMap) {
        var flags = dayCalendarFlags(dayDate, today, holidayMap);
        var html = '<div class="automation-cal-week-day-col' + (flags.className ? " " + flags.className : "") + '">';
        html += '<div class="relative" style="height:' + gridHeight + 'px">';
        slotStarts.forEach(function(slotMinutes, slotIndex) {
            var slotClasses = "automation-cal-week-slot";
            if (slotIndex > 0 && slotMinutes % 60 === 0) slotClasses += " is-hour";
            html += '<div class="' + slotClasses + '" style="height:' + calendarRowHeight() + 'px"></div>';
        });
        if (sameDay(dayDate, today)) {
            var nowMinutes = minutesFromDate(new Date());
            if (nowMinutes >= WEEK_DAY_START_MINUTES && nowMinutes <= WEEK_DAY_END_MINUTES) {
                var nowTop = ((nowMinutes - WEEK_DAY_START_MINUTES) / WEEK_SLOT_MINUTES) * calendarRowHeight();
                html += '<div class="automation-cal-week-now-line" style="top:' + nowTop + 'px"></div>';
            }
        }
        weekTimedEventsForDay(dayDate).forEach(function(evt) {
            var at = evt.at instanceof Date ? evt.at : new Date(evt.at);
            if (isNaN(at.getTime())) return;
            var minute = minutesFromDate(at);
            var top = ((minute - WEEK_DAY_START_MINUTES) / WEEK_SLOT_MINUTES) * calendarRowHeight();
            var blockHeight = evt.compact ? Math.max(18, calendarRowHeight() - 6) : Math.max(28, calendarRowHeight() - 4);
            var name = escapeAttr(evt.automation.name || "Automation");
            var timeLabel = escapeAttr(evt.compact ? evt.label : formatTimeShort(at));
            var blockClasses = "automation-cal-week-block";
            if (evt.compact) blockClasses += " is-compact";
            html += '<button type="button" class="' + blockClasses + '" data-id="' + escapeAttr(evt.automation.id) + '" style="top:' + top + "px;height:" + blockHeight + 'px" title="' + name + " — " + timeLabel + '">';
            html += '<span class="automation-cal-week-block-time">' + timeLabel + "</span>";
            html += '<span class="automation-cal-week-block-title">' + name + "</span>";
            html += "</button>";
        });
        html += "</div></div>";
        return html;
    }

    function renderTimeGridCalendar(dayCount) {
        var el = document.getElementById("automation-calendar");
        if (!el) return;
        el.className = "flex-1 min-h-0 flex flex-col overflow-hidden";
        var periodStart = calendarMode === "day" ? startOfDay(calendarAnchor) : startOfWeek(calendarAnchor);
        var today = startOfDay(new Date());
        var holidayMap = holidayMapForRange(periodStart, dayCount);
        var slotStarts = weekSlotStarts();
        var gridHeight = calendarRowHeight() * slotStarts.length;
        var colsClass = dayCount === 1 ? " is-cols-1" : "";
        var innerClass = "automation-cal-week-timegrid-inner" + (dayCount === 1 ? " is-day-view" : "");
        var html = '<div class="automation-cal-week-timegrid"><div class="' + innerClass + '">';
        html += '<div class="automation-cal-week-header' + colsClass + '">';
        html += '<div class="automation-cal-week-header-time">Time</div>';
        for (var h = 0; h < dayCount; h += 1) {
            var headerDay = addDays(periodStart, h);
            var headerFlags = dayCalendarFlags(headerDay, today, holidayMap);
            html += '<div class="automation-cal-week-header-day' + (headerFlags.className ? " " + headerFlags.className : "") + '">';
            html += '<div class="automation-cal-week-header-weekday">' + WEEKDAY_LABELS[headerDay.getDay()] + "</div>";
            html += '<div class="automation-cal-week-header-date-row">';
            html += '<span class="automation-cal-week-header-date">' + headerDay.toLocaleDateString([], { day: "numeric", month: "short" }) + "</span>";
            if (headerFlags.holiday) {
                html += '<span class="automation-cal-week-header-holiday">' + escapeAttr(headerFlags.holiday) + "</span>";
            }
            html += "</div>";
            html += "</div>";
        }
        html += "</div>";
        html += '<div class="automation-cal-week-body-grid' + colsClass + '">';
        html += '<div class="automation-cal-week-time-gutter">';
        slotStarts.forEach(function(slotMinutes) {
            html += '<div class="automation-cal-week-time-label" style="height:' + calendarRowHeight() + 'px">';
            if (slotMinutes % 60 === 0) {
                html += "<span>" + escapeAttr(formatHourLabel(slotMinutes)) + "</span>";
            }
            html += "</div>";
        });
        html += "</div>";
        for (var i = 0; i < dayCount; i += 1) {
            html += renderTimeGridDayColumn(addDays(periodStart, i), slotStarts, gridHeight, today, holidayMap);
        }
        html += "</div></div></div>";
        el.innerHTML = html;
        var scrollRoot = el.querySelector(".automation-cal-week-timegrid");
        bindWeekCalendarClicks(el);
        scrollTimeGridCalendarToNow(scrollRoot, periodStart, dayCount);
        if (window.AutomationCalendarBlocks) {
            window.AutomationCalendarBlocks.afterTimeGridRender(el, periodStart, dayCount);
        }
    }

    function parseScheduleTimeOnDay(dayDate, timeStr) {
        var d = startOfDay(dayDate);
        var parts = String(timeStr || "09:00").split(":");
        d.setHours(parseInt(parts[0], 10) || 0, parseInt(parts[1], 10) || 0, 0, 0);
        return d;
    }

    function intervalToMs(schedule) {
        var value = Math.max(1, parseInt(schedule.interval, 10) || 15);
        var unit = String(schedule.interval_unit || "minutes").toLowerCase();
        if (schedule.kind === "15min" || schedule.kind === "15m") return 15 * 60 * 1000;
        if (schedule.kind === "30min" || schedule.kind === "30m") return 30 * 60 * 1000;
        return unit === "seconds" ? value * 1000 : value * 60 * 1000;
    }

    function weeklyDaysMatch(dayDate, schedule) {
        var raw = String((schedule && schedule.days) || "1");
        var days = raw.split(",").map(function(part) {
            return parseInt(part.trim(), 10);
        }).filter(function(n) { return !isNaN(n); });
        if (!days.length) days = [1];
        return days.indexOf(dayDate.getDay()) !== -1;
    }

    function getAutomationOccurrencesForDay(automation, dayDate) {
        if (!automation || automation.status !== "active") return [];
        var schedule = automation.schedule || {};
        var kind = normalizeScheduleKind(schedule.kind || schedule.frequency || "daily");
        var dayStart = startOfDay(dayDate);
        var dayEnd = addDays(dayStart, 1);
        var out = [];

        if (kind === "once") {
            if (!schedule.run_at) return [];
            var runAt = new Date(schedule.run_at);
            if (!isNaN(runAt.getTime()) && runAt >= dayStart && runAt < dayEnd) {
                out.push({ at: runAt, label: formatTimeShort(runAt), compact: false });
            }
            return out;
        }
        if (kind === "daily") {
            var dailyAt = parseScheduleTimeOnDay(dayStart, schedule.time);
            out.push({ at: dailyAt, label: formatTimeShort(dailyAt), compact: false });
            return out;
        }
        if (kind === "weekly") {
            if (!weeklyDaysMatch(dayStart, schedule)) return [];
            var weeklyAt = parseScheduleTimeOnDay(dayStart, schedule.time);
            out.push({ at: weeklyAt, label: formatTimeShort(weeklyAt), compact: false });
            return out;
        }
        if (kind === "monthly") {
            if (!monthlyDaysMatch(dayStart, schedule)) return [];
            var monthlyAt = parseScheduleTimeOnDay(dayStart, schedule.time);
            out.push({ at: monthlyAt, label: formatTimeShort(monthlyAt), compact: false });
            return out;
        }
        if (kind === "hourly") {
            out.push({
                at: dayStart,
                label: "Hourly",
                compact: true,
                sortKey: 0
            });
            return out;
        }
        if (kind === "interval") {
            var intervalMs = intervalToMs(schedule);
            if (!intervalMs) return [];
            var cursor = automation.next_run_at ? new Date(automation.next_run_at) : new Date();
            if (isNaN(cursor.getTime())) cursor = new Date();
            while (cursor.getTime() > dayStart.getTime()) {
                cursor = new Date(cursor.getTime() - intervalMs);
            }
            while (cursor.getTime() < dayStart.getTime()) {
                cursor = new Date(cursor.getTime() + intervalMs);
            }
            var cap = 0;
            while (cursor < dayEnd && cap < 48) {
                out.push({ at: new Date(cursor), label: formatTimeShort(cursor), compact: false });
                cursor = new Date(cursor.getTime() + intervalMs);
                cap += 1;
            }
            if (out.length > 6) {
                return [{
                    at: out[0].at,
                    label: scheduleLabel(schedule),
                    compact: true,
                    sortKey: out[0].at.getTime()
                }];
            }
            return out;
        }
        return out;
    }

    function calendarEventsForDay(dayDate) {
        var events = [];
        automationsData.forEach(function(automation) {
            getAutomationOccurrencesForDay(automation, dayDate).forEach(function(occ) {
                events.push({
                    automation: automation,
                    at: occ.at,
                    label: occ.label,
                    compact: !!occ.compact,
                    sortKey: occ.sortKey != null ? occ.sortKey : occ.at.getTime()
                });
            });
        });
        events.sort(function(a, b) {
            return a.sortKey - b.sortKey || String(a.automation.name || "").localeCompare(String(b.automation.name || ""));
        });
        return events;
    }

    function setMainView(view) {
        mainView = view === "calendar" ? "calendar" : "automations";
        var listBtn = document.getElementById("automation-view-list");
        var calBtn = document.getElementById("automation-view-calendar");
        if (listBtn) {
            listBtn.classList.toggle("is-active", mainView === "automations");
            listBtn.setAttribute("aria-selected", mainView === "automations" ? "true" : "false");
        }
        if (calBtn) {
            calBtn.classList.toggle("is-active", mainView === "calendar");
            calBtn.setAttribute("aria-selected", mainView === "calendar" ? "true" : "false");
        }
        renderMainWorkspace();
    }

    function renderMainWorkspace() {
        var calPanel = document.getElementById("automation-calendar-panel");
        var empty = document.getElementById("automation-empty");
        var detail = document.getElementById("automation-detail");
        if (mainView === "calendar") {
            if (calPanel) calPanel.classList.remove("hidden");
            if (empty) empty.classList.add("hidden");
            if (detail) detail.classList.add("hidden");
            renderCalendar();
            return;
        }
        if (calPanel) calPanel.classList.add("hidden");
        if (currentAutomationId || isCreating) {
            if (empty) empty.classList.add("hidden");
            if (detail) detail.classList.remove("hidden");
        } else {
            if (empty) empty.classList.remove("hidden");
            if (detail) detail.classList.add("hidden");
        }
    }

    function setCalendarMode(mode) {
        if (mode === "week" || mode === "day") {
            calendarMode = mode;
        } else {
            calendarMode = "month";
        }
        if (calendarMode === "week") {
            calendarAnchor = startOfWeek(calendarAnchor);
        } else if (calendarMode === "day") {
            calendarAnchor = startOfDay(new Date());
        } else {
            calendarAnchor = startOfMonth(calendarAnchor);
        }
        var monthBtn = document.getElementById("automation-cal-mode-month");
        var weekBtn = document.getElementById("automation-cal-mode-week");
        var dayBtn = document.getElementById("automation-cal-mode-day");
        if (monthBtn) monthBtn.classList.toggle("is-active", calendarMode === "month");
        if (weekBtn) weekBtn.classList.toggle("is-active", calendarMode === "week");
        if (dayBtn) dayBtn.classList.toggle("is-active", calendarMode === "day");
        updateCalendarZoomUi();
        renderCalendar();
    }

    function shiftCalendarPeriod(delta) {
        if (calendarMode === "week") {
            calendarAnchor = addDays(calendarAnchor, delta * 7);
        } else if (calendarMode === "day") {
            calendarAnchor = addDays(calendarAnchor, delta);
        } else {
            var next = new Date(calendarAnchor.getFullYear(), calendarAnchor.getMonth() + delta, 1);
            calendarAnchor = startOfDay(next);
        }
        renderCalendar();
    }

    function renderCalendarHelp() {
        var helpEl = document.getElementById("automation-cal-help");
        if (!helpEl) return;
        if (calendarMode === "day") {
            helpEl.textContent = "Day view shows one day in 15-minute rows. Use the zoom controls beside Month/Week/Day to make the grid taller or shorter. Drag on empty space to create a time block. Overlapping blocks sit side by side in columns so you can run parallel work. Drag a block to move it, or drag its top or bottom edge to change the duration. Double-click a block to edit it, link a board and ticket, or naturalize the times. Orange chips are scheduled automations — click one to open it. Use Start to track live work on the current block.";
            return;
        }
        if (calendarMode === "week") {
            helpEl.textContent = "Week view shows seven days side by side. Use the zoom controls beside Month/Week/Day to make the grid taller or shorter. Drag on empty space to create a time block for that day and time. Overlapping blocks in the same period appear in side-by-side columns. Drag blocks to reschedule them, or resize from the top or bottom edge. Double-click a block to edit board, ticket, and title. Orange chips are scheduled automations — click one to open it. The hours total above counts time blocks in the visible week.";
            return;
        }
        helpEl.textContent = "Month view shows scheduled automations and time blocks across the month. Orange chips are automations — click one to open it. Colored blocks are time blocks linked to boards. Double-click a day to add a new time block. Switch to Week or Day to drag-create blocks on the time grid.";
    }

    function renderCalendarTitle() {
        var titleEl = document.getElementById("automation-cal-title");
        if (!titleEl) return;
        if (calendarMode === "day") {
            titleEl.textContent = startOfDay(calendarAnchor).toLocaleDateString([], {
                weekday: "short",
                month: "short",
                day: "numeric",
                year: "numeric"
            });
            return;
        }
        if (calendarMode === "week") {
            var weekStart = startOfWeek(calendarAnchor);
            var weekEnd = addDays(weekStart, 6);
            titleEl.textContent = weekStart.toLocaleDateString([], { month: "short", day: "numeric" }) +
                " – " + weekEnd.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
            return;
        }
        titleEl.textContent = calendarAnchor.toLocaleDateString([], { month: "long", year: "numeric" });
    }

    function renderMonthCalendar() {
        var el = document.getElementById("automation-calendar");
        if (!el) return;
        el.className = "flex-1 min-h-0 flex flex-col overflow-hidden";
        var monthStart = startOfMonth(calendarAnchor);
        var gridStart = startOfWeek(monthStart);
        var dayCount = monthGridDayCount(monthStart, gridStart);
        var today = startOfDay(new Date());
        var holidayMap = holidayMapForRange(gridStart, dayCount);
        var html = '<div class="automation-cal-month-shell">';
        html += '<div class="automation-cal-weekdays">';
        WEEKDAY_LABELS.forEach(function(label) {
            html += '<div class="automation-cal-weekday">' + label + "</div>";
        });
        html += "</div><div class=\"automation-cal-month-grid\">";
        for (var i = 0; i < dayCount; i += 1) {
            var dayDate = addDays(gridStart, i);
            var inMonth = dayDate.getMonth() === monthStart.getMonth();
            var events = calendarEventsForDay(dayDate);
            var dayFlags = dayCalendarFlags(dayDate, today, holidayMap);
            var classes = "automation-cal-day";
            if (!inMonth) classes += " is-outside";
            if (dayFlags.className) classes += " " + dayFlags.className;
            html += '<div class="' + classes + '" data-date-key="' + escapeAttr(toDateKey(dayDate)) + '">';
            html += '<div class="automation-cal-day-head">';
            html += '<span class="automation-cal-day-num">' + dayDate.getDate() + "</span>";
            if (dayFlags.holiday && inMonth) {
                html += '<span class="automation-cal-day-holiday">' + escapeAttr(dayFlags.holiday) + "</span>";
            }
            html += "</div>";
            html += '<div class="automation-cal-day-stack">';
            events.slice(0, 3).forEach(function(evt) {
                var name = escapeAttr(evt.automation.name || "Automation");
                var timeLabel = escapeAttr(evt.compact ? (evt.label || "Scheduled") : (evt.label || formatTimeShort(evt.at)));
                var fullTitle = escapeAttr((evt.compact ? (evt.label || "Scheduled") : (evt.label ? evt.label + " " : "")) + (evt.automation.name || "Automation"));
                html += '<button type="button" class="automation-cal-event" data-id="' + escapeAttr(evt.automation.id) + '" title="' + fullTitle + '">' +
                    '<span class="automation-cal-event-title">' + name + "</span>" +
                    '<span class="automation-cal-event-time">' + timeLabel + "</span>" +
                    "</button>";
            });
            if (events.length > 3) {
                html += '<div class="automation-cal-more">+' + (events.length - 3) + " more</div>";
            }
            html += "</div></div>";
        }
        html += "</div></div>";
        el.innerHTML = html;
        var monthGrid = el.querySelector(".automation-cal-month-grid");
        if (monthGrid) {
            monthGrid.style.setProperty("--cal-month-rows", String(Math.ceil(dayCount / 7)));
        }
        el.querySelectorAll(".automation-cal-event").forEach(function(btn) {
            btn.addEventListener("click", function() {
                openAutomationFromCalendar(btn.getAttribute("data-id"));
            });
        });
        if (window.AutomationCalendarBlocks) {
            window.AutomationCalendarBlocks.afterMonthRender(el);
        }
    }

    function renderWeekCalendar() {
        renderTimeGridCalendar(7);
    }

    function renderDayCalendar() {
        renderTimeGridCalendar(1);
    }

    function openAutomationFromCalendar(id) {
        if (!id) return;
        setMainView("automations");
        selectAutomation(id);
    }

    function renderCalendar() {
        renderCalendarTitle();
        renderCalendarHelp();
        updateCalendarZoomUi();
        var blocksReady = window.AutomationCalendarBlocks
            ? window.AutomationCalendarBlocks.loadForVisibleRange(calendarMode, calendarAnchor)
            : Promise.resolve();
        blocksReady
            .catch(function() {})
            .then(function() {
                if (calendarMode === "week") renderWeekCalendar();
                else if (calendarMode === "day") renderDayCalendar();
                else renderMonthCalendar();
        });
    }

    function renderList(data) {
        var el = document.getElementById("automation-list");
        if (!el) return;
        if (!Array.isArray(data)) data = [];
        automationsData = data;
        if (!data.length) {
            el.innerHTML = '<p class="text-sm text-gray-400">No automations yet. Create one with Add Automation.</p>';
            return;
        }
        el.innerHTML = data.map(function(a) {
            var active = currentAutomationId === a.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            var status = a.status === "active" ? "text-emerald-300" : "text-gray-500";
            return '<div class="automation-item-wrapper flex items-center gap-1 rounded border' + active + ' group focus:outline-none focus:ring-2 focus:ring-[#f97316]/60" data-id="' + escapeAttr(a.id) + '" tabindex="0" role="option" aria-selected="' + (currentAutomationId === a.id ? "true" : "false") + '">' +
                '<button type="button" class="automation-item flex-1 min-w-0 text-left px-3 py-2 text-white text-sm" data-id="' + escapeAttr(a.id) + '">' +
                    '<span class="block truncate">' + escapeAttr(a.name || "Untitled Automation") + '</span>' +
                    '<span class="block truncate text-xs text-gray-500 mt-0.5">' + escapeAttr(scheduleLabel(a.schedule)) + '</span>' +
                '</button>' +
                '<span class="px-2 text-xs ' + status + '">' + escapeAttr(a.status || "active") + '</span>' +
            '</div>';
        }).join("");
        el.querySelectorAll(".automation-item").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                selectAutomation(btn.getAttribute("data-id"));
            });
        });
        el.querySelectorAll(".automation-item-wrapper").forEach(function(row) {
            row.addEventListener("focus", function() {
                var id = row.getAttribute("data-id");
                if (id && currentAutomationId !== id) selectAutomation(id);
            });
        });
    }

    function showEmpty() {
        currentAutomationId = null;
        isCreating = false;
        renderList(automationsData);
        renderMainWorkspace();
    }

    function showDetail() {
        renderMainWorkspace();
    }

    function setValue(id, value) {
        var el = document.getElementById(id);
        if (el) el.value = value == null ? "" : String(value);
    }

    function setAutomationStatus(status) {
        var hidden = document.getElementById("automation-status");
        var toggle = document.getElementById("automation-status-switch");
        if (!hidden || !toggle) return;
        var isActive = String(status || "active").toLowerCase() !== "paused";
        hidden.value = isActive ? "active" : "paused";
        toggle.classList.toggle("is-on", isActive);
        toggle.setAttribute("aria-checked", isActive ? "true" : "false");
        toggle.setAttribute("aria-label", isActive ? "Active" : "Inactive");
        var text = toggle.querySelector(".automation-status-switch-text");
        if (text) text.textContent = isActive ? "Active" : "Inactive";
    }

    function toggleAutomationStatus() {
        var hidden = document.getElementById("automation-status");
        if (!hidden) return;
        setAutomationStatus(hidden.value === "active" ? "paused" : "active");
    }

    function fillForm(row) {
        row = row || emptyAutomation();
        var schedule = row.schedule || {};
        var kind = normalizeScheduleKind(schedule.kind || schedule.frequency || "daily");
        var interval = schedule.interval || 15;
        var intervalUnit = schedule.interval_unit || "minutes";
        if (schedule.kind === "15min" || schedule.kind === "15m") {
            interval = 15;
            intervalUnit = "minutes";
        } else if (schedule.kind === "30min" || schedule.kind === "30m") {
            interval = 30;
            intervalUnit = "minutes";
        }
        setValue("automation-id", row.id || "");
        setValue("automation-name", row.name || "");
        setValue("automation-instruction", row.instruction || "");
        setValue("automation-kind", kind);
        setValue("automation-time", schedule.time || "09:00");
        setValue("automation-once-at", String(schedule.run_at || ""));
        setValue("automation-interval-value", interval);
        setValue("automation-interval-unit", intervalUnit === "seconds" ? "seconds" : "minutes");
        setWeeklyDaySelection(kind === "weekly" ? (schedule.days || "1") : "1");
        renderMonthlyDayBadges(
            kind === "monthly"
                ? String(schedule.days || "1").split(",").map(function(part) { return part.trim(); }).filter(Boolean)
                : []
        );
        var monthlyInput = document.getElementById("automation-monthly-days-input");
        if (monthlyInput) monthlyInput.value = "";
        setAutomationStatus(row.status || "active");
        document.getElementById("automation-detail-title").textContent = row.id ? (row.name || "Untitled Automation") : "New Automation";
        document.getElementById("automation-detail-meta").textContent = row.id ? ("Next run: " + shortDate(row.next_run_at)) : "Not saved yet";
        document.getElementById("automation-delete").disabled = !row.id;
        document.getElementById("automation-run").disabled = !row.id;
        updateScheduleControls();
    }

    function renderRuns(runs) {
        var el = document.getElementById("automation-runs");
        if (!el) return;
        if (!currentAutomationId) {
            el.innerHTML = '<p class="text-xs text-gray-500">Save an automation to see runs.</p>';
            return;
        }
        if (!runs || !runs.length) {
            el.innerHTML = '<p class="text-xs text-gray-500">No runs yet.</p>';
            return;
        }
        el.innerHTML = runs.map(function(run) {
            var chat = run.chat_id ? " · chat #" + run.chat_id : "";
            return '<div class="rounded border border-white/10 bg-[#152054] px-3 py-2">' +
                '<div class="flex items-center justify-between gap-2">' +
                    '<span class="text-sm text-white">' + escapeAttr(run.status || "recorded") + '</span>' +
                    '<span class="text-xs text-gray-500">' + escapeAttr(shortDate(run.started_at)) + '</span>' +
                '</div>' +
                '<div class="text-xs text-gray-400 mt-1">' + escapeAttr((run.summary || "Automation run recorded.") + chat) + '</div>' +
            '</div>';
        }).join("");
    }

    function selectAutomation(id) {
        var row = automationsData.filter(function(a) { return a.id === id; })[0];
        if (!row) {
            showEmpty();
            return;
        }
        if (mainView === "calendar") {
            setMainView("automations");
        }
        currentAutomationId = id;
        isCreating = false;
        fillForm(row);
        showDetail();
        setActiveTab("details");
        renderList(automationsData);
        apiFetch("/api/automations/" + encodeURIComponent(id) + "/runs")
            .then(function(data) { renderRuns(data.runs || []); })
            .catch(function() { renderRuns([]); });
    }

    function createNewAutomation() {
        setMainView("automations");
        currentAutomationId = null;
        isCreating = true;
        fillForm(emptyAutomation());
        showDetail();
        renderRuns([]);
        renderList(automationsData);
        var name = document.getElementById("automation-name");
        if (name) name.focus();
        setActiveTab("details");
    }

    function payloadFromForm() {
        var kind = document.getElementById("automation-kind").value || "daily";
        var schedule = { kind: kind };
        if (kind === "once") {
            var runAt = document.getElementById("automation-once-at").value;
            if (runAt) schedule.run_at = runAt;
        } else if (kind === "interval") {
            var intervalValue = parseInt(document.getElementById("automation-interval-value").value, 10);
            schedule.interval = intervalValue > 0 ? intervalValue : 15;
            schedule.interval_unit = document.getElementById("automation-interval-unit").value || "minutes";
        } else if (kind === "daily" || kind === "weekly" || kind === "monthly") {
            var time = document.getElementById("automation-time").value;
            if (time) schedule.time = time;
            if (kind === "weekly") {
                schedule.days = getWeeklyDaysCsv();
            } else if (kind === "monthly") {
                var monthDays = getMonthlyDaysArray();
                schedule.days = monthDays.length ? monthDays.join(",") : "1";
            }
        }
        return {
            name: (document.getElementById("automation-name").value || "Untitled Automation").trim(),
            automation_type: "scheduled_instruction",
            status: document.getElementById("automation-status").value || "active",
            instruction: (document.getElementById("automation-instruction").value || "").trim(),
            schedule: schedule
        };
    }

    function softRefreshCurrentAutomation() {
        if (!currentAutomationId) return Promise.resolve();
        return apiFetch("/api/automations/" + encodeURIComponent(currentAutomationId))
            .then(function(data) {
                var automation = data.automation;
                if (!automation) return;
                var idx = automationsData.findIndex(function(a) { return a.id === automation.id; });
                if (idx >= 0) {
                    automationsData[idx] = automation;
                }
                fillForm(automation);
                renderList(automationsData);
                if (mainView === "calendar") renderCalendar();
                if (Array.isArray(data.runs)) {
                    renderRuns(data.runs);
                }
            })
            .catch(function() {});
    }

    function connectAutomationUpdatesSocket() {
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + location.host + "/api/workflows/ws";
        var ws;
        var reconnectTimer;
        function connect() {
            if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
            try {
                ws = new WebSocket(url);
            } catch (_) {
                reconnectTimer = setTimeout(connect, 5000);
                return;
            }
            ws.onmessage = function(evt) {
                try {
                    var msg = JSON.parse(evt.data);
                    if (msg.type === "workflow_updated") {
                        loadAutomations(true);
                        softRefreshCurrentAutomation();
                    }
                } catch (_) {}
            };
            ws.onclose = function() {
                ws = null;
                reconnectTimer = setTimeout(connect, 5000);
            };
        }
        connect();
    }

    function loadAutomations(skipAutoSelect) {
        var list = document.getElementById("automation-list");
        if (list) list.innerHTML = '<p class="text-sm text-gray-400">Loading automations...</p>';
        return apiFetch("/api/automations")
            .then(function(data) {
                automationsData = data.automations || [];
                renderList(automationsData);
                if (mainView === "calendar") {
                    renderMainWorkspace();
                    return;
                }
                if (currentAutomationId && automationsData.some(function(a) { return a.id === currentAutomationId; })) {
                    selectAutomation(currentAutomationId);
                } else if (!skipAutoSelect && automationsData.length) {
                    selectAutomation(automationsData[0].id);
                } else if (!isCreating) {
                    showEmpty();
                }
            })
            .catch(function(e) {
                if (list) list.innerHTML = '<p class="text-sm text-amber-400">Could not load automations.</p>';
                showSnackbar(e.message || "Could not load automations", "error");
            });
    }

    function saveAutomation(evt) {
        evt.preventDefault();
        var id = document.getElementById("automation-id").value;
        var method = id ? "PUT" : "POST";
        var path = id ? "/api/automations/" + encodeURIComponent(id) : "/api/automations";
        apiFetch(path, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payloadFromForm())
        }).then(function(data) {
            currentAutomationId = (data.automation || {}).id || id;
            isCreating = false;
            showSnackbar("Automation saved", "success");
            return loadAutomations(true);
        }).catch(function(e) {
            showSnackbar(e.message || "Could not save automation", "error");
        });
    }

    function resolveAutomationId(idOverride) {
        if (typeof idOverride === "string" && idOverride.trim()) {
            return idOverride.trim();
        }
        var fromField = document.getElementById("automation-id");
        return fromField ? String(fromField.value || "").trim() : "";
    }

    function deleteSelected(idOverride) {
        var id = resolveAutomationId(idOverride) || currentAutomationId;
        if (!id) return;
        var row = automationsData.filter(function(a) { return a.id === id; })[0];
        if (!row) {
            row = {
                id: id,
                name: (document.getElementById("automation-name").value || "").trim() || "Untitled Automation",
            };
        }
        window.DecisionsAPI.confirm({
            title: "Remove automation",
            message: 'Remove automation "' + (row.name || "Untitled Automation") + '"? This cannot be undone.',
            confirmLabel: "Remove",
            danger: true,
            onConfirm: function() {
                apiFetch("/api/automations/" + encodeURIComponent(id), { method: "DELETE" })
                    .then(function() {
                        currentAutomationId = null;
                        isCreating = false;
                        showSnackbar("Automation removed", "success");
                        return loadAutomations();
                    })
                    .catch(function(e) { showSnackbar(e.message || "Could not remove automation", "error"); });
            }
        });
    }

    function runSelected() {
        var id = document.getElementById("automation-id").value;
        if (!id) {
            showSnackbar("Save the automation before running it.", "error");
            return;
        }
        apiFetch("/api/automations/" + encodeURIComponent(id) + "/run", { method: "POST" })
            .then(function(data) {
                showSnackbar("Automation run recorded", "success");
                setActiveTab("history");
                renderRuns(data && data.run ? [data.run] : []);
                return loadAutomations(true).then(function() {
                    setActiveTab("history");
                    return apiFetch("/api/automations/" + encodeURIComponent(id) + "/runs")
                        .then(function(runsData) { renderRuns(runsData.runs || []); })
                        .catch(function() {});
                });
            })
            .catch(function(e) { showSnackbar(e.message || "Could not run automation", "error"); });
    }

    function bindKeyboard() {
        if (!window.DecisionsListKeyboard) return;
        window.DecisionsListKeyboard.bind({
            listEl: "automation-list",
            namespace: "automations",
            rowSelector: ".automation-item-wrapper",
            getRowId: function(row) { return row.getAttribute("data-id"); },
            getSelectedId: function() {
                var field = document.getElementById("automation-id");
                return (field && field.value) || currentAutomationId || null;
            },
            onSelect: function(id) { selectAutomation(id); },
            onDelete: function(id) { deleteSelected(id); },
            pageGuard: function() { return !!document.getElementById("automation-list"); },
            shouldSkip: function(e) { return automationKeyboardTargetIsEditable(e.target); },
        });
    }

    function setActiveTab(tab) {
        tab = tab === "history" ? "history" : "details";
        document.querySelectorAll(".automation-tab").forEach(function(btn) {
            var active = btn.getAttribute("data-tab") === tab;
            btn.classList.toggle("border-[#f97316]", active);
            btn.classList.toggle("border-transparent", !active);
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
        });
        document.querySelectorAll(".automation-tab-pane").forEach(function(pane) {
            pane.classList.toggle("hidden", pane.id !== "automation-tab-" + tab);
        });
    }

    function updateScheduleControls() {
        if (window.DecisionsDateTime && window.DecisionsDateTime.close) {
            window.DecisionsDateTime.close();
        }
        var kindEl = document.getElementById("automation-kind");
        var timeWrap = document.getElementById("automation-time-wrap");
        var onceWrap = document.getElementById("automation-once-wrap");
        var intervalWrap = document.getElementById("automation-interval-wrap");
        var weeklyWrap = document.getElementById("automation-weekly-days-wrap");
        var monthlyWrap = document.getElementById("automation-monthly-days-wrap");
        var time = document.getElementById("automation-time");
        var onceAt = document.getElementById("automation-once-at");
        if (!kindEl || !timeWrap || !onceWrap || !intervalWrap) return;
        var kind = kindEl.value || "daily";
        var showTime = kind === "daily" || kind === "weekly" || kind === "monthly";
        timeWrap.classList.toggle("hidden", !showTime);
        onceWrap.classList.toggle("hidden", kind !== "once");
        intervalWrap.classList.toggle("hidden", kind !== "interval");
        if (weeklyWrap) weeklyWrap.classList.toggle("hidden", kind !== "weekly");
        if (monthlyWrap) monthlyWrap.classList.toggle("hidden", kind !== "monthly");
        if (time) time.disabled = !showTime;
        if (onceAt) onceAt.disabled = kind !== "once";
        [time, onceAt].forEach(function(el) {
            if (!el) return;
            if (window.DecisionsDateTime) window.DecisionsDateTime.refreshInput(el);
        });
    }

    function bind() {
        var listViewBtn = document.getElementById("automation-view-list");
        var calendarViewBtn = document.getElementById("automation-view-calendar");
        if (listViewBtn) listViewBtn.addEventListener("click", function() { setMainView("automations"); });
        if (calendarViewBtn) calendarViewBtn.addEventListener("click", function() { setMainView("calendar"); });
        var calPrev = document.getElementById("automation-cal-prev");
        var calNext = document.getElementById("automation-cal-next");
        if (calPrev) calPrev.addEventListener("click", function() { shiftCalendarPeriod(-1); });
        if (calNext) calNext.addEventListener("click", function() { shiftCalendarPeriod(1); });
        var calMonth = document.getElementById("automation-cal-mode-month");
        var calWeek = document.getElementById("automation-cal-mode-week");
        var calDay = document.getElementById("automation-cal-mode-day");
        if (calMonth) calMonth.addEventListener("click", function() { setCalendarMode("month"); });
        if (calWeek) calWeek.addEventListener("click", function() { setCalendarMode("week"); });
        if (calDay) calDay.addEventListener("click", function() { setCalendarMode("day"); });
        var calZoomOut = document.getElementById("automation-cal-zoom-out");
        var calZoomIn = document.getElementById("automation-cal-zoom-in");
        if (calZoomOut) {
            calZoomOut.addEventListener("click", function() {
                setCalendarGridZoom(CALENDAR_GRID_ZOOM - CALENDAR_GRID_ZOOM_STEP);
            });
        }
        if (calZoomIn) {
            calZoomIn.addEventListener("click", function() {
                setCalendarGridZoom(CALENDAR_GRID_ZOOM + CALENDAR_GRID_ZOOM_STEP);
            });
        }
        var calToday = document.getElementById("automation-cal-today");
        if (calToday) calToday.addEventListener("click", goToCalendarToday);
        var calExport = document.getElementById("automation-cal-export");
        if (calExport) calExport.addEventListener("click", openExportModal);
        var exportCancel = document.getElementById("sched-export-cancel");
        if (exportCancel) exportCancel.addEventListener("click", closeExportModal);
        var exportDownload = document.getElementById("sched-export-download");
        if (exportDownload) exportDownload.addEventListener("click", downloadTimesheetExport);
        var exportSelectAll = document.getElementById("sched-export-select-all");
        if (exportSelectAll) exportSelectAll.addEventListener("click", function() { setExportBoardChecks(true); });
        var exportDeselectAll = document.getElementById("sched-export-deselect-all");
        if (exportDeselectAll) exportDeselectAll.addEventListener("click", function() { setExportBoardChecks(false); });
        var exportModal = document.getElementById("sched-export-modal");
        if (exportModal) {
            exportModal.addEventListener("click", function(e) {
                if (e.target === exportModal) closeExportModal();
            });
            var exportPanel = exportModal.querySelector("div");
            if (exportPanel) {
                exportPanel.addEventListener("click", function(e) {
                    e.stopPropagation();
                });
            }
        }
        document.getElementById("automation-new").addEventListener("click", createNewAutomation);
        document.getElementById("automation-create-big").addEventListener("click", createNewAutomation);
        document.getElementById("automation-detail").addEventListener("submit", saveAutomation);
        document.getElementById("automation-delete").addEventListener("click", function() {
            deleteSelected();
        });
        document.getElementById("automation-run").addEventListener("click", runSelected);
        document.getElementById("automation-kind").addEventListener("change", updateScheduleControls);
        document.querySelectorAll(".automation-weekday-btn").forEach(function(btn) {
            btn.addEventListener("click", function() {
                btn.classList.toggle("is-selected");
            });
        });
        var monthlyInput = document.getElementById("automation-monthly-days-input");
        if (monthlyInput) {
            monthlyInput.addEventListener("keydown", function(e) {
                if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addMonthlyDaysFromInput();
                }
            });
            monthlyInput.addEventListener("blur", addMonthlyDaysFromInput);
        }
        var statusSwitch = document.getElementById("automation-status-switch");
        if (statusSwitch) {
            statusSwitch.addEventListener("click", function() {
                toggleAutomationStatus();
            });
        }
        document.querySelectorAll(".automation-tab").forEach(function(btn) {
            btn.addEventListener("click", function() {
                setActiveTab(btn.getAttribute("data-tab"));
            });
        });
        bindKeyboard();
    }

    document.addEventListener("DOMContentLoaded", function() {
        calendarAnchor = startOfDay(new Date());
        loadCalendarGridZoom();
        bind();
        if (window.AutomationCalendarBlocks) {
            window.AutomationCalendarBlocks.init({
                apiFetch: apiFetch,
                getRowHeight: calendarRowHeight,
                showSnackbar: showSnackbar,
                onChanged: function() {
                    return loadAutomations(true).then(function() {
                        if (mainView === "calendar") renderCalendar();
                    });
                }
            });
        }
        connectAutomationUpdatesSocket();
        loadAutomations(location.hash === "#calendar").then(function() {
            if (location.hash === "#calendar") {
                setMainView("calendar");
            }
        });
        try {
            var prefill = sessionStorage.getItem("automation_prefill_instruction");
            if (prefill) {
                sessionStorage.removeItem("automation_prefill_instruction");
                createNewAutomation();
                setValue("automation-instruction", prefill);
                setValue("automation-name", prefill.split(/\s+/).slice(0, 6).join(" "));
            }
        } catch (e) {}
    });
})();
