/**
 * Tensology-style schedule blocks for the automations calendar.
 */
(function () {
    "use strict";

    var SLOT_MINUTES = 15;
    var DAY_START = 0;
    var DAY_END = 24 * 60;
    var BASE_ROW_HEIGHT = 10;
    var MIN_BLOCK_HEIGHT = 18;
    var getRowHeight = function() {
        return BASE_ROW_HEIGHT;
    };
    var LOCAL_BOARD_COLOR = "#f97316";
    var TRELLO_BOARD_COLOR = "#0079bf";
    var JIRA_BOARD_COLOR = "#0052cc";
    var blocks = [];
    var runningTimer = null;
    var snapToFifteen = true;
    var dragState = null;
    var moveState = null;
    var resizeState = null;
    var boardOptions = [];
    var boardTickets = [];
    var ticketLoadToken = 0;
    var editorBlockId = null;
    var apiFetch = null;
    var showSnackbar = null;
    var onChanged = null;
    var columnRefs = [];
    var gridRoot = null;
    var gridPeriodStart = null;
    var gridDayCount = 0;
    var gridMode = null;
    var timerTickHandle = null;
    var timerNowAt = new Date();
    var monthResizeFrame = null;
    var monthResizeBound = false;
    var monthResizeObserver = null;
    var MONTH_DAY_MIN_HEIGHT = 72;
    var blockContextMenuState = null;
    var visibleRangeStart = null;
    var visibleRangeEnd = null;
    var suppressBlockClick = false;
    var timerBlockWarnAt = 0;

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function normalizeHexColor(value) {
        var raw = String(value || "").trim();
        if (!raw) return "";
        if (!raw.startsWith("#")) raw = "#" + raw;
        if (!/^#[0-9a-fA-F]{3,8}$/.test(raw)) return "";
        if (raw.length === 4) {
            return "#" + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3];
        }
        return raw.slice(0, 7);
    }

    function colorIsLight(hex) {
        var color = normalizeHexColor(hex);
        if (!color) return true;
        var r = parseInt(color.slice(1, 3), 16);
        var g = parseInt(color.slice(3, 5), 16);
        var b = parseInt(color.slice(5, 7), 16);
        var luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance >= 0.62;
    }

    function defaultBoardColor(provider) {
        if (provider === "trello") return TRELLO_BOARD_COLOR;
        if (provider === "jira") return JIRA_BOARD_COLOR;
        return LOCAL_BOARD_COLOR;
    }

    function boardColorForProvider(provider, color) {
        return normalizeHexColor(color) || defaultBoardColor(provider);
    }

    function resolveBlockBoardColor(block) {
        if (!block) return "";
        var direct = normalizeHexColor(block.board_color);
        if (direct) return direct;
        var value = boardValueForBlock(block);
        if (value) {
            var opt = boardOptions.filter(function(o) { return o.value === value; })[0];
            if (opt) return opt.color || defaultBoardColor(opt.board_provider);
        }
        if (block.board_id || block.external_board_id || block.ticket_id) {
            return defaultBoardColor(block.board_provider || "local");
        }
        return "";
    }

    function enrichBlockBoardColor(block) {
        if (!block) return block;
        if (!block.board_color) {
            block.board_color = resolveBlockBoardColor(block);
        }
        return block;
    }

    function boardColorMap() {
        var map = {};
        boardOptions.forEach(function(opt) {
            if (opt.value) map[opt.value] = opt.color || defaultBoardColor(opt.board_provider);
        });
        return map;
    }

    function refreshBoardSelectUi() {
        var select = document.getElementById("sched-block-board");
        if (!select) return;
        var previousValue = select.value;
        select.innerHTML = renderBoardSelectHtml();
        if (previousValue) select.value = previousValue;
        if (!window.KanbanCustomSelect) return;
        if (!select._kbCustomSelect) {
            window.KanbanCustomSelect.upgrade(select, { placeholder: "Choose board..." });
        }
        if (select._kbCustomSelect) {
            select._kbCustomSelect.setColorValues(boardColorMap());
            select._kbCustomSelect.refresh();
        }
    }

    function blockSurfaceStyle(block) {
        if (block && block.is_timer_running) {
            return {
                background: "#22c55e",
                color: "#052e16",
                border: "1px solid #16a34a",
                durationBg: "rgba(5, 46, 22, 0.18)",
                durationColor: "#052e16",
                handleBg: "rgba(5, 46, 22, 0.18)"
            };
        }
        var boardColor = resolveBlockBoardColor(block);
        if (!boardColor) {
            return {
                background: "#ffffff",
                color: "#111827",
                border: "1px solid #d1d5db",
                durationBg: "rgba(17, 24, 39, 0.08)",
                durationColor: "#111827",
                handleBg: "rgba(17, 24, 39, 0.12)"
            };
        }
        var light = colorIsLight(boardColor);
        return {
            background: boardColor,
            color: light ? "#111827" : "#ffffff",
            border: "1px solid " + (light ? "rgba(17, 24, 39, 0.12)" : "rgba(255, 255, 255, 0.22)"),
            durationBg: light ? "rgba(17, 24, 39, 0.1)" : "rgba(0, 0, 0, 0.24)",
            durationColor: light ? "#111827" : "#ffffff",
            handleBg: light ? "rgba(17, 24, 39, 0.12)" : "rgba(255, 255, 255, 0.24)"
        };
    }

    function overlapPositionStyle(column, columnCount) {
        column = column || 0;
        columnCount = columnCount || 1;
        if (columnCount <= 1) {
            return {
                left: "2px",
                width: "calc(100% - 4px)",
                right: "auto",
                zIndex: "4"
            };
        }
        var widthPercent = 100 / columnCount;
        var leftPercent = column * widthPercent;
        return {
            left: "calc(" + leftPercent + "% + 2px)",
            width: "calc(" + widthPercent + "% - 4px)",
            right: "auto",
            zIndex: String(4 + column)
        };
    }

    function applyOverlapPosition(el, column, columnCount) {
        if (!el) return;
        var pos = overlapPositionStyle(column, columnCount);
        el.style.left = pos.left;
        el.style.width = pos.width;
        el.style.right = pos.right;
        el.style.zIndex = pos.zIndex;
    }

    function blockProjectKey(block) {
        if (!block) return null;
        var pid = block.effective_project_id != null ? block.effective_project_id : block.project_id;
        if (pid == null || pid === "") return null;
        return String(pid);
    }

    function timedRangesOverlap(a, b) {
        return a.startMin < b.endMin && a.endMin > b.startMin;
    }

    function timedItemsAllowSideBySide(a, b) {
        if (!timedRangesOverlap(a, b)) return false;
        var keyA = a.projectKey;
        var keyB = b.projectKey;
        if (keyA && keyB && keyA === keyB) return false;
        return true;
    }

    function hasSameProjectTimeOverlap(blockId, dayDate, startMinute, endMinute, projectKey) {
        if (!projectKey) return false;
        return blocksForDay(dayDate).some(function(block) {
            if (block.id === blockId) return false;
            if (blockProjectKey(block) !== projectKey) return false;
            var blockStart = minutesFromDate(parseBlockDate(block.start_at));
            var blockEnd = minutesFromDate(parseBlockDate(block.end_at));
            return startMinute < blockEnd && endMinute > blockStart;
        });
    }

    function sameProjectOverlapMessage() {
        return "This project already has a time block during that period.";
    }

    function timedItemFromElement(el) {
        var top = parseFloat(el.style.top);
        var height = parseFloat(el.style.height);
        if (isNaN(top)) top = 0;
        if (isNaN(height) || height <= 0) height = MIN_BLOCK_HEIGHT;
        var startMin = DAY_START + (top / getRowHeight()) * SLOT_MINUTES;
        var endMin = DAY_START + ((top + height) / getRowHeight()) * SLOT_MINUTES;
        if (endMin <= startMin) endMin = startMin + SLOT_MINUTES;
        return {
            el: el,
            startMin: startMin,
            endMin: endMin,
            top: top,
            height: height,
            column: 0,
            columnCount: 1,
            projectKey: el.getAttribute("data-project-id") || null
        };
    }

    function layoutTimedItems(items) {
        if (!items.length) return items;
        items.sort(function(a, b) {
            if (a.startMin !== b.startMin) return a.startMin - b.startMin;
            if (a.endMin !== b.endMin) return a.endMin - b.endMin;
            var aId = parseInt(a.el.getAttribute("data-id"), 10) || 0;
            var bId = parseInt(b.el.getAttribute("data-id"), 10) || 0;
            return aId - bId;
        });

        var active = [];
        var clusterIndices = [];
        var clusterMaxColumns = 1;

        function finalizeCluster() {
            clusterIndices.forEach(function(index) {
                items[index].columnCount = clusterMaxColumns;
            });
            clusterIndices = [];
            clusterMaxColumns = 1;
        }

        items.forEach(function(item, sortedIndex) {
            active = active.filter(function(entry) {
                return entry.endMin > item.startMin;
            });
            if (!active.length) finalizeCluster();

            var usedColumns = {};
            active.forEach(function(entry) {
                if (timedItemsAllowSideBySide(entry, item)) {
                    usedColumns[entry.column] = true;
                }
            });
            var column = 0;
            while (usedColumns[column]) column += 1;

            item.column = column;
            active.push({
                sortedIndex: sortedIndex,
                startMin: item.startMin,
                endMin: item.endMin,
                column: column,
                projectKey: item.projectKey
            });
            clusterIndices.push(sortedIndex);
            clusterMaxColumns = Math.max(clusterMaxColumns, active.length);
        });
        finalizeCluster();
        return items;
    }

    function applyColumnOverlapLayout(colEl) {
        if (!colEl) return;
        var items = [];
        colEl.querySelectorAll(".sched-block, .automation-cal-week-block").forEach(function(el) {
            items.push(timedItemFromElement(el));
        });
        if (!items.length) return;
        layoutTimedItems(items);
        items.forEach(function(item) {
            applyOverlapPosition(item.el, item.column, item.columnCount);
        });
    }

    function blockInlineStyle(block, layoutStyle) {
        var surface = blockSurfaceStyle(block);
        return "top:" + layoutStyle.top + "px;height:" + Math.max(MIN_BLOCK_HEIGHT, layoutStyle.height || 0) + "px;" +
            "background:" + surface.background + ";" +
            "color:" + surface.color + ";" +
            "border:" + surface.border + ";" +
            "--sched-block-duration-bg:" + surface.durationBg + ";" +
            "--sched-block-duration-color:" + surface.durationColor + ";" +
            "--sched-block-handle-bg:" + surface.handleBg + ";";
    }

    function startOfDay(value) {
        var d = value instanceof Date ? new Date(value.getTime()) : new Date(value);
        d.setHours(0, 0, 0, 0);
        return d;
    }

    function addDays(value, days) {
        var d = startOfDay(value);
        d.setDate(d.getDate() + days);
        return d;
    }

    function sameDay(a, b) {
        return startOfDay(a).getTime() === startOfDay(b).getTime();
    }

    function minutesFromDate(value) {
        return (value.getHours() * 60) + value.getMinutes();
    }

    function dateWithMinutes(day, totalMinutes) {
        var d = new Date(day);
        d.setHours(0, 0, 0, 0);
        d.setMinutes(totalMinutes, 0, 0);
        return d;
    }

    function toIsoLocal(dt) {
        var y = dt.getFullYear();
        var m = String(dt.getMonth() + 1).padStart(2, "0");
        var d = String(dt.getDate()).padStart(2, "0");
        var hh = String(dt.getHours()).padStart(2, "0");
        var mm = String(dt.getMinutes()).padStart(2, "0");
        return y + "-" + m + "-" + d + "T" + hh + ":" + mm;
    }

    function toDateOnly(dt) {
        if (!dt) return "";
        var y = dt.getFullYear();
        var m = String(dt.getMonth() + 1).padStart(2, "0");
        var d = String(dt.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    function parseBlockDate(value) {
        if (!value) return new Date(NaN);
        var raw = String(value).trim();
        if (raw.endsWith("Z")) raw = raw.slice(0, -1);
        var offsetMatch = raw.match(/([+-]\d{2}:\d{2})$/);
        if (offsetMatch) raw = raw.slice(0, -offsetMatch[1].length);
        var match = raw.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
        if (match) {
            return new Date(
                parseInt(match[1], 10),
                parseInt(match[2], 10) - 1,
                parseInt(match[3], 10),
                parseInt(match[4], 10),
                parseInt(match[5], 10),
                0,
                0
            );
        }
        return new Date(raw);
    }

    function setDateTimeField(id, date) {
        var el = document.getElementById(id);
        if (!el || !date) return;
        var parsed = date instanceof Date ? date : parseBlockDate(date);
        var value = toIsoLocal(parsed);
        el.value = value;
        if (window.DecisionsDateTime && typeof window.DecisionsDateTime.refreshInput === "function") {
            window.DecisionsDateTime.refreshInput(el);
        }
    }

    function interactionIncrement() {
        return snapToFifteen ? SLOT_MINUTES : 1;
    }

    function snapInteractionMinute(minute) {
        var clamped = Math.max(DAY_START, Math.min(DAY_END, Math.round(minute)));
        if (!snapToFifteen) return clamped;
        var snapped = Math.floor((clamped - DAY_START) / SLOT_MINUTES) * SLOT_MINUTES + DAY_START;
        return Math.max(DAY_START, Math.min(DAY_END, snapped));
    }

    function clampStartMinute(value, durationMinutes) {
        durationMinutes = durationMinutes || interactionIncrement();
        var maxStart = Math.max(DAY_START, DAY_END - durationMinutes);
        return Math.max(DAY_START, Math.min(value, maxStart));
    }

    function snapMinute(minute, intent) {
        if (!snapToFifteen) return Math.max(DAY_START, Math.min(DAY_END, Math.round(minute)));
        var slot = Math.floor(minute / SLOT_MINUTES);
        if (intent === "end") slot += 1;
        return Math.max(DAY_START, Math.min(DAY_END, slot * SLOT_MINUTES));
    }

    function formatTimeFromMinutes(totalMinutes) {
        var hours = Math.floor(totalMinutes / 60);
        var minutes = totalMinutes % 60;
        return String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0");
    }

    function formatBlockDurationMinutes(minutes) {
        var safeMinutes = Math.max(0, Math.round(minutes));
        var hours = Math.floor(safeMinutes / 60);
        var remainingMinutes = safeMinutes % 60;
        if (hours > 0 && remainingMinutes > 0) return hours + "h " + remainingMinutes + "m";
        if (hours > 0) return hours + "h";
        return remainingMinutes + "m";
    }

    function formatSecondsDuration(totalSeconds) {
        var safeSeconds = Math.max(0, Math.floor(totalSeconds));
        var hours = Math.floor(safeSeconds / 3600);
        var minutes = Math.floor((safeSeconds % 3600) / 60);
        var seconds = safeSeconds % 60;
        return String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
    }

    function blockEndDate(block) {
        if (block.is_timer_running) {
            return new Date(Math.max(parseBlockDate(block.end_at).getTime(), timerNowAt.getTime()));
        }
        return parseBlockDate(block.end_at);
    }

    function blockDurationMinutes(block) {
        var start = parseBlockDate(block.start_at);
        var end = blockEndDate(block);
        return Math.max(0, Math.round((end.getTime() - start.getTime()) / 60000));
    }

    function totalSecondsForDay(dayDate) {
        var total = 0;
        blocksForDay(dayDate).forEach(function(block) {
            var start = parseBlockDate(block.start_at);
            var end = blockEndDate(block);
            total += Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
        });
        return total;
    }

    function totalSecondsForRange(periodStart, dayCount) {
        var total = 0;
        for (var i = 0; i < dayCount; i += 1) {
            total += totalSecondsForDay(addDays(periodStart, i));
        }
        return total;
    }

    function previewStyle(startMinute, endMinute) {
        var top = ((startMinute - DAY_START) / SLOT_MINUTES) * getRowHeight();
        var bottom = ((endMinute - DAY_START) / SLOT_MINUTES) * getRowHeight();
        return { top: top, height: Math.max(MIN_BLOCK_HEIGHT, bottom - top) };
    }

    function clearInteractionPreviews(root) {
        if (!root) return;
        root.querySelectorAll(".sched-interaction-preview").forEach(function(node) { node.remove(); });
        root.querySelectorAll(".sched-block.is-moving, .sched-block.is-resizing").forEach(function(el) {
            el.classList.remove("is-moving", "is-resizing");
        });
    }

    function setInteracting(active) {
        document.body.classList.toggle("sched-calendar-interacting", !!active);
    }

    function markInteractingBlocks(root) {
        if (!root) return;
        root.querySelectorAll(".sched-block.is-moving, .sched-block.is-resizing").forEach(function(el) {
            el.classList.remove("is-moving", "is-resizing");
        });
        if (moveState) {
            var movingEl = root.querySelector('.sched-block[data-id="' + moveState.blockId + '"]');
            if (movingEl) movingEl.classList.add("is-moving");
        }
        if (resizeState) {
            var resizingEl = root.querySelector('.sched-block[data-id="' + resizeState.blockId + '"]');
            if (resizingEl) resizingEl.classList.add("is-resizing");
        }
    }

    function paintInteractionPreviews(root) {
        clearInteractionPreviews(root);
        if (!root) return;
        markInteractingBlocks(root);
        if (dragState) {
            var dragCol = columnRefs[dragState.dayIndex];
            if (!dragCol || !dragCol.el) return;
            var dragStart = Math.min(dragState.anchorMinute, dragState.currentMinute);
            var dragEnd = Math.min(DAY_END, Math.max(dragState.anchorMinute, dragState.currentMinute) + interactionIncrement());
            var dragStyle = previewStyle(dragStart, dragEnd);
            dragCol.el.insertAdjacentHTML("beforeend",
                '<div class="sched-interaction-preview sched-drag-preview" style="top:' + dragStyle.top + "px;height:" + dragStyle.height + 'px">' +
                '<span class="sched-preview-duration">' + escapeAttr(formatBlockDurationMinutes(dragEnd - dragStart)) + "</span>" +
                '<span class="sched-preview-time">' + escapeAttr(formatTimeFromMinutes(dragStart) + " - " + formatTimeFromMinutes(dragEnd)) + "</span>" +
                "</div>");
            return;
        }
        if (moveState) {
            var moveCol = columnRefs[moveState.dayIndex];
            if (!moveCol || !moveCol.el || moveState.currentMinute == null) return;
            var moveEnd = moveState.currentMinute + moveState.duration;
            var moveStyle = previewStyle(moveState.currentMinute, moveEnd);
            moveCol.el.insertAdjacentHTML("beforeend",
                '<div class="sched-interaction-preview sched-move-preview" style="top:' + moveStyle.top + "px;height:" + moveStyle.height + 'px">' +
                '<span class="sched-preview-duration">' + escapeAttr(formatBlockDurationMinutes(moveState.duration)) + "</span>" +
                '<span class="sched-preview-time">' + escapeAttr(formatTimeFromMinutes(moveState.currentMinute) + " - " + formatTimeFromMinutes(moveEnd)) + "</span>" +
                "</div>");
            return;
        }
        if (resizeState) {
            var resizeCol = columnRefs[resizeState.dayIndex] ||
                columnRefs.filter(function(c) { return sameDay(c.dayDate, resizeState.dayDate); })[0];
            if (!resizeCol || !resizeCol.el) return;
            var resizeStart = Math.min(resizeState.startMinute, resizeState.endMinute);
            var resizeEnd = Math.max(resizeState.startMinute, resizeState.endMinute);
            if (resizeEnd <= resizeStart) resizeEnd = resizeStart + interactionIncrement();
            var resizeStyle = previewStyle(resizeStart, resizeEnd);
            resizeCol.el.insertAdjacentHTML("beforeend",
                '<div class="sched-interaction-preview sched-resize-preview" style="top:' + resizeStyle.top + "px;height:" + resizeStyle.height + 'px">' +
                '<span class="sched-preview-duration">' + escapeAttr(formatBlockDurationMinutes(resizeEnd - resizeStart)) + "</span>" +
                '<span class="sched-preview-time">' + escapeAttr(formatTimeFromMinutes(resizeStart) + " - " + formatTimeFromMinutes(resizeEnd)) + "</span>" +
                "</div>");
        }
    }

    function updateHoursSummary(root, periodStart, dayCount, mode) {
        if (!root) return;
        root.querySelectorAll(".automation-cal-week-header-day").forEach(function(header, index) {
            if (index >= dayCount) return;
            var dayDate = addDays(periodStart, index);
            var hoursEl = header.querySelector(".sched-day-hours");
            if (!hoursEl) {
                hoursEl = document.createElement("div");
                hoursEl.className = "sched-day-hours";
                header.appendChild(hoursEl);
            }
            hoursEl.textContent = formatSecondsDuration(totalSecondsForDay(dayDate));
        });
        var totalEl = document.getElementById("sched-block-hours-total-value");
        var totalLabel = document.getElementById("sched-block-hours-total-label");
        if (totalEl) {
            totalEl.textContent = formatSecondsDuration(totalSecondsForRange(periodStart, dayCount));
        }
        if (totalLabel) {
            totalLabel.textContent = mode === "day" ? "Day total" : (mode === "week" ? "Week total" : "Month total");
        }
    }

    function paintMonthHours(root) {
        if (!root) return;
        root.querySelectorAll(".automation-cal-day").forEach(function(cell) {
            var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
            if (isNaN(dayDate.getTime())) return;
            var hoursEl = cell.querySelector(".sched-month-day-hours");
            if (!hoursEl) {
                hoursEl = document.createElement("div");
                hoursEl.className = "sched-month-day-hours";
                cell.appendChild(hoursEl);
            }
            var seconds = totalSecondsForDay(dayDate);
            hoursEl.textContent = seconds > 0 ? formatSecondsDuration(seconds) : "";
        });
    }

    function refreshVisibleHours() {
        if (!gridRoot) return;
        if (gridMode === "month") {
            paintMonthBlocks(gridRoot);
            paintMonthHours(gridRoot);
            scheduleMonthCalendarResize(gridRoot);
            var monthTotalSeconds = 0;
            gridRoot.querySelectorAll(".automation-cal-day[data-date-key]").forEach(function(cell) {
                var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
                if (!isNaN(dayDate.getTime())) monthTotalSeconds += totalSecondsForDay(dayDate);
            });
            var totalEl = document.getElementById("sched-block-hours-total-value");
            var totalLabel = document.getElementById("sched-block-hours-total-label");
            if (totalEl) totalEl.textContent = formatSecondsDuration(monthTotalSeconds);
            if (totalLabel) totalLabel.textContent = "Month total";
            return;
        }
        if (gridPeriodStart != null && gridDayCount > 0) {
            paintBlocks(gridRoot, gridPeriodStart, gridDayCount);
            updateHoursSummary(gridRoot, gridPeriodStart, gridDayCount, gridMode || "week");
        }
    }

    function startLiveTimerTick() {
        if (timerTickHandle) return;
        timerTickHandle = window.setInterval(function() {
            timerNowAt = new Date();
            updateTimerElapsedLabel();
            refreshVisibleHours();
        }, 1000);
    }

    function stopLiveTimerTick() {
        if (!timerTickHandle) return;
        window.clearInterval(timerTickHandle);
        timerTickHandle = null;
    }

    function updateTimerElapsedLabel() {
        var elapsedEl = document.getElementById("sched-block-timer-elapsed");
        if (!elapsedEl) return;
        if (!runningTimer) {
            elapsedEl.classList.add("hidden");
            elapsedEl.textContent = "00:00:00";
            return;
        }
        var start = parseBlockDate(runningTimer.start_at);
        var seconds = Math.max(0, Math.floor((timerNowAt.getTime() - start.getTime()) / 1000));
        elapsedEl.textContent = formatSecondsDuration(seconds);
        elapsedEl.classList.remove("hidden");
    }

    function blocksForDay(dayDate) {
        var dayStart = startOfDay(dayDate);
        var dayEnd = addDays(dayStart, 1);
        return blocks.filter(function(block) {
            var start = parseBlockDate(block.start_at);
            var end = parseBlockDate(block.end_at);
            return start < dayEnd && end > dayStart;
        });
    }

    function blockStyleForDay(block, dayDate) {
        var start = parseBlockDate(block.start_at);
        var end = blockEndDate(block);
        var dayStart = startOfDay(dayDate);
        var visibleStart = start < dayStart ? dayStart : start;
        var dayEnd = addDays(dayStart, 1);
        var visibleEnd = end > dayEnd ? dayEnd : end;
        var top = ((minutesFromDate(visibleStart) - DAY_START) / SLOT_MINUTES) * getRowHeight();
        var bottom = ((minutesFromDate(visibleEnd) - DAY_START) / SLOT_MINUTES) * getRowHeight();
        return {
            top: top,
            height: Math.max(MIN_BLOCK_HEIGHT, bottom - top)
        };
    }

    function getPointerDayAndTime(clientX, clientY, intent) {
        if (!columnRefs.length) return null;
        var best = null;
        var bestDistance = Infinity;
        for (var i = 0; i < columnRefs.length; i += 1) {
            var col = columnRefs[i];
            if (!col.el) continue;
            var rect = col.el.getBoundingClientRect();
            var distanceX = clientX < rect.left ? rect.left - clientX : (clientX > rect.right ? clientX - rect.right : 0);
            if (distanceX < bestDistance) {
                bestDistance = distanceX;
                best = { col: col, index: i, rect: rect };
            }
        }
        if (!best) return null;
        var offsetY = Math.max(0, Math.min(best.rect.height, clientY - best.rect.top));
        var slotIndex = Math.max(0, Math.floor(offsetY / getRowHeight()));
        var exactMinute = DAY_START + ((offsetY / getRowHeight()) * SLOT_MINUTES);
        var snappedMinute = DAY_START + ((slotIndex + (intent === "end" ? 1 : 0)) * SLOT_MINUTES);
        var minute = snapToFifteen ? snappedMinute : Math.round(exactMinute);
        minute = Math.max(DAY_START, Math.min(DAY_END, minute));
        return { dayIndex: best.index, dayDate: best.col.dayDate, minute: minute };
    }

    function normalizeScheduleBoardOption(provider, board) {
        if (!board) return null;
        if (provider === "local") {
            if (board.id == null) return null;
            return {
                label: board.name || ("Board " + board.id),
                board_id: board.id,
                board_provider: "local",
                external_board_id: null,
                color: boardColorForProvider("local", board.color),
                value: "local:" + board.id
            };
        }
        var externalId = String(board.id != null ? board.id : board.external_board_id || "").trim();
        if (!externalId) return null;
        return {
            label: board.name || externalId,
            board_id: board.local_id || null,
            board_provider: provider,
            external_board_id: externalId,
            color: boardColorForProvider(provider, board.color),
            value: provider + ":" + externalId
        };
    }

    function renderBoardSelectHtml() {
        var groups = [
            { key: "local", label: "Local", match: function(opt) { return opt.board_provider === "local"; } },
            { key: "trello", label: "Trello", match: function(opt) { return opt.board_provider === "trello"; } },
            { key: "jira", label: "Jira", match: function(opt) { return opt.board_provider === "jira"; } }
        ];
        var html = '<option value="">Choose board...</option>';
        groups.forEach(function(group) {
            var items = boardOptions.filter(group.match);
            if (!items.length) return;
            html += '<optgroup label="' + escapeAttr(group.label) + '">';
            html += items.map(function(opt) {
                return '<option value="' + escapeAttr(opt.value) + '">' + escapeAttr(opt.label) + "</option>";
            }).join("");
            html += "</optgroup>";
        });
        return html;
    }

    function loadBoards() {
        return Promise.all([
            apiFetch("/api/tickets/boards").catch(function() { return []; }),
            apiFetch("/api/tickets/external-boards").catch(function() { return { trello: [], jira: [] }; })
        ]).then(function(results) {
            var boards = Array.isArray(results[0]) ? results[0] : [];
            var external = results[1] || {};
            boardOptions = [];
            var seen = {};

            function pushOption(opt) {
                if (!opt || !opt.value || seen[opt.value]) return;
                seen[opt.value] = true;
                boardOptions.push(opt);
            }

            boards.filter(function(b) { return (b.source || "database") === "database"; }).forEach(function(b) {
                pushOption(normalizeScheduleBoardOption("local", b));
            });
            (external.trello || []).forEach(function(b) {
                pushOption(normalizeScheduleBoardOption("trello", b));
            });
            (external.jira || []).forEach(function(b) {
                pushOption(normalizeScheduleBoardOption("jira", b));
            });
            boards.filter(function(b) {
                var source = (b.source || "").toLowerCase();
                return source === "trello" || source === "jira";
            }).forEach(function(b) {
                pushOption(normalizeScheduleBoardOption(b.source, {
                    id: b.external_board_id || b.id,
                    name: b.name,
                    local_id: b.id,
                    color: b.color
                }));
            });
            refreshBoardSelectUi();
        });
    }

    function ticketDisplayLabel(ticket, provider) {
        var title = String(ticket.title || ticket.name || "").trim();
        if (provider === "jira") {
            var key = String(ticket.key || ticket.ticket_key || ticket.id || "").trim();
            if (key && title) return key + " — " + title;
            return key || title || "Untitled ticket";
        }
        if (title) return title;
        if (ticket.id != null && ticket.id !== "") return "Ticket " + ticket.id;
        return "Untitled ticket";
    }

    function lanesFromBoardData(data) {
        return (data && (data.lanes || data.columns || data.lists)) || [];
    }

    function fetchBoardDetail(opt, attempt) {
        attempt = attempt || 0;
        if (opt.board_provider === "local") {
            return apiFetch("/api/tickets/boards/" + encodeURIComponent(opt.board_id));
        }
        var forceQ = attempt === 0 ? "?force_refresh=1" : "";
        return apiFetch(
            "/api/tickets/external-boards/" + encodeURIComponent(opt.board_provider) + "/" +
            encodeURIComponent(opt.external_board_id) + forceQ
        ).then(function(data) {
            if (data && data.cache_ready === false && attempt < 60) {
                return new Promise(function(resolve) {
                    window.setTimeout(function() { resolve(fetchBoardDetail(opt, attempt + 1)); }, 800);
                });
            }
            return data;
        });
    }

    function renderTicketSelectHtml(lanes, provider) {
        boardTickets = [];
        var html = '<option value="">Choose ticket...</option>';
        lanes.forEach(function(lane) {
            var tickets = Array.isArray(lane.tickets) ? lane.tickets.slice() : [];
            if (window.KanbanTicketUi && window.KanbanTicketUi.compareTicketsForListView) {
                tickets.sort(window.KanbanTicketUi.compareTicketsForListView);
            }
            if (!tickets.length) return;
            var laneLabel = String(lane.name || "Lane").trim() || "Lane";
            html += '<optgroup label="' + escapeAttr(laneLabel) + '">';
            tickets.forEach(function(ticket) {
                var idx = boardTickets.length;
                var isLocal = provider === "local";
                boardTickets.push({
                    ticket_id: isLocal ? (ticket.id || null) : null,
                    external_ticket_key: isLocal ? null : String(ticket.key || ticket.ticket_key || ticket.id || ""),
                    label: ticketDisplayLabel(ticket, provider)
                });
                html += '<option value="' + idx + '">' + escapeAttr(boardTickets[idx].label) + "</option>";
            });
            html += "</optgroup>";
        });
        return html;
    }

    function boardValueForBlock(block) {
        if (!block || !block.board_provider) return "";
        if (block.board_provider === "local" && block.board_id) {
            return "local:" + block.board_id;
        }
        var externalId = block.external_board_id || (block.board_provider !== "local" ? block.board_id : null);
        if (externalId) return block.board_provider + ":" + externalId;
        return "";
    }

    function loadTicketsForBoard(value) {
        var token = ++ticketLoadToken;
        var opt = boardOptions.filter(function(o) { return o.value === value; })[0];
        var select = document.getElementById("sched-block-ticket");
        if (!select) return Promise.resolve();
        if (!opt) {
            boardTickets = [];
            select.innerHTML = '<option value="">Choose ticket...</option>';
            return Promise.resolve();
        }
        select.innerHTML = '<option value="">Loading tickets...</option>';
        return fetchBoardDetail(opt, 0).then(function(data) {
            if (token !== ticketLoadToken) return;
            var lanes = lanesFromBoardData(data);
            if (!lanes.length) {
                boardTickets = [];
                select.innerHTML = '<option value="">No tickets found</option>';
                return;
            }
            select.innerHTML = renderTicketSelectHtml(lanes, opt.board_provider);
            if (boardTickets.length === 0) {
                select.innerHTML = '<option value="">No tickets found</option>';
            }
        }).catch(function() {
            if (token !== ticketLoadToken) return;
            boardTickets = [];
            select.innerHTML = '<option value="">Could not load tickets</option>';
        });
    }

    function setEditorActionsOpen(open) {
        var menu = document.getElementById("sched-block-actions-menu");
        var btn = document.getElementById("sched-block-options-btn");
        if (menu) menu.classList.toggle("hidden", !open);
        if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function openEditor(block, startDate, endDate) {
        closeBlockContextMenu();
        setEditorActionsOpen(false);
        editorBlockId = block ? block.id : null;
        var modal = document.getElementById("sched-block-modal");
        if (!modal) return;
        document.getElementById("sched-block-title").value = block ? (block.title || "") : "";
        setDateTimeField("sched-block-start", startDate);
        setDateTimeField("sched-block-end", endDate);
        document.getElementById("sched-block-modal-title").textContent = block ? "Edit time block" : "New time block";
        var optionsBtn = document.getElementById("sched-block-options-btn");
        if (optionsBtn) optionsBtn.classList.toggle("hidden", !block);
        var boardSelect = document.getElementById("sched-block-board");
        var ticketSelect = document.getElementById("sched-block-ticket");
        if (boardSelect) boardSelect.value = "";
        if (ticketSelect) ticketSelect.innerHTML = '<option value="">Choose ticket...</option>';
        if (block && block.board_provider) {
            var val = boardValueForBlock(block);
            if (val && boardSelect) boardSelect.value = val;
            if (window.KanbanCustomSelect) window.KanbanCustomSelect.refreshById("sched-block-board");
            if (val) {
                loadTicketsForBoard(val).then(function() {
                    if (!ticketSelect || !block) return;
                    var idx = boardTickets.findIndex(function(t) {
                        if (block.ticket_id && t.ticket_id) return String(t.ticket_id) === String(block.ticket_id);
                        return t.external_ticket_key && block.external_ticket_key &&
                            String(t.external_ticket_key) === String(block.external_ticket_key);
                    });
                    if (idx >= 0) ticketSelect.value = String(idx);
                });
            }
        }
        modal.classList.remove("hidden");
    }

    function closeEditor() {
        var modal = document.getElementById("sched-block-modal");
        if (modal) modal.classList.add("hidden");
        setEditorActionsOpen(false);
        editorBlockId = null;
    }

    function selectedBoardPayload() {
        var boardSelect = document.getElementById("sched-block-board");
        var ticketSelect = document.getElementById("sched-block-ticket");
        var opt = boardOptions.filter(function(o) { return o.value === (boardSelect && boardSelect.value); })[0];
        var payload = {
            board_id: null,
            board_provider: "local",
            external_board_id: null,
            ticket_id: null,
            external_ticket_key: null
        };
        if (!opt) return payload;
        payload.board_id = opt.board_id;
        payload.board_provider = opt.board_provider;
        payload.external_board_id = opt.external_board_id;
        if (ticketSelect && ticketSelect.value !== "") {
            var ticket = boardTickets[parseInt(ticketSelect.value, 10)];
            if (ticket) {
                payload.ticket_id = ticket.ticket_id;
                payload.external_ticket_key = ticket.external_ticket_key;
            }
        }
        return payload;
    }

    function saveEditor() {
        var title = (document.getElementById("sched-block-title").value || "").trim() || "Untitled block";
        var startAt = document.getElementById("sched-block-start").value;
        var endAt = document.getElementById("sched-block-end").value;
        var link = selectedBoardPayload();
        var payload = Object.assign({
            title: title,
            start_at: startAt,
            end_at: endAt
        }, link);
        var req = editorBlockId
            ? apiFetch("/api/schedule-blocks/" + encodeURIComponent(editorBlockId), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
            : apiFetch("/api/schedule-blocks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        req.then(function() {
            closeEditor();
            showSnackbar("Time block saved", "success");
            return reloadScheduleBlocks();
        }).catch(function(e) {
            showSnackbar(e.message || "Could not save time block", "error");
        });
    }

    function deleteBlockById(blockId, closeEditorAfter) {
        if (!blockId) return;
        apiFetch("/api/schedule-blocks/" + encodeURIComponent(blockId), { method: "DELETE" })
            .then(function() {
                if (closeEditorAfter) closeEditor();
                showSnackbar("Time block removed", "success");
                return reloadScheduleBlocks();
            })
            .catch(function(e) { showSnackbar(e.message || "Could not remove block", "error"); });
    }

    function deleteEditorBlock() {
        if (!editorBlockId) return;
        deleteBlockById(editorBlockId, true);
    }

    function mergeBlockFromApi(updated) {
        if (!updated || updated.id == null) return;
        enrichBlockBoardColor(updated);
        var idx = -1;
        for (var i = 0; i < blocks.length; i++) {
            if (blocks[i].id === updated.id) {
                idx = i;
                break;
            }
        }
        if (idx >= 0) blocks[idx] = updated;
        else blocks.push(updated);
        repaintScheduleBlocks();
    }

    function naturalizeBlockById(blockId, updateEditorFields) {
        if (!blockId) return Promise.resolve();
        return apiFetch("/api/schedule-blocks/" + encodeURIComponent(blockId) + "/naturalize-time", { method: "POST" })
            .then(function(data) {
                if (data.block) {
                    mergeBlockFromApi(data.block);
                    if (updateEditorFields) {
                        setDateTimeField("sched-block-start", data.block.start_at);
                        setDateTimeField("sched-block-end", data.block.end_at);
                    }
                }
                showSnackbar(data.message || "Time block naturalized", "success");
                return refreshTimerStatus();
            })
            .catch(function(e) {
                showSnackbar(e.message || "Could not naturalize block", "error");
            });
    }

    function naturalizeEditorBlock() {
        if (!editorBlockId) return;
        naturalizeBlockById(editorBlockId, true);
    }

    function closeBlockContextMenu() {
        blockContextMenuState = null;
        var menu = document.getElementById("sched-block-context-menu");
        if (menu) menu.classList.add("hidden");
    }

    function openBlockContextMenu(blockId, clientX, clientY) {
        if (runningTimer) return;
        var menu = document.getElementById("sched-block-context-menu");
        if (!menu) return;
        blockContextMenuState = { blockId: blockId };
        menu.classList.remove("hidden");
        var width = menu.offsetWidth || 176;
        var height = menu.offsetHeight || 120;
        var left = Math.max(8, Math.min(clientX, window.innerWidth - width - 8));
        var top = Math.max(8, Math.min(clientY, window.innerHeight - height - 8));
        menu.style.left = left + "px";
        menu.style.top = top + "px";
    }

    function warnTimerBlocksCalendarDrag() {
        var now = Date.now();
        if (now - timerBlockWarnAt < 3000) return;
        timerBlockWarnAt = now;
        if (typeof showSnackbar === "function") {
            showSnackbar("Stop the timer before you can move or edit time blocks.", "warning");
        }
    }

    function blockCalendarDragWhileTimerRunning() {
        if (!runningTimer) return false;
        warnTimerBlocksCalendarDrag();
        return true;
    }

    function refreshTimerStatus() {
        return apiFetch("/api/schedule-blocks/timer").then(function(data) {
            runningTimer = data.running ? data.block : null;
            timerNowAt = new Date();
            var btn = document.getElementById("sched-block-timer-btn");
            var label = document.getElementById("sched-block-timer-label");
            if (btn) btn.textContent = runningTimer ? "Stop" : "Start";
            if (label) label.textContent = runningTimer ? "Timer running" : "Timer stopped";
            if (btn) btn.classList.toggle("is-running", !!runningTimer);
            updateTimerElapsedLabel();
            if (runningTimer) startLiveTimerTick();
            else stopLiveTimerTick();
        }).catch(function() {});
    }

    function toggleTimer() {
        if (runningTimer) {
            return apiFetch("/api/schedule-blocks/timer/stop", { method: "POST" })
                .then(function() { showSnackbar("Timer stopped", "success"); return reloadScheduleBlocks(); })
                .catch(function(e) { showSnackbar(e.message || "Could not stop timer", "error"); });
        }
        return apiFetch("/api/schedule-blocks/timer/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Timer" })
        }).then(function() {
            showSnackbar("Timer started", "success");
            return reloadScheduleBlocks();
        }).catch(function(e) { showSnackbar(e.message || "Could not start timer", "error"); });
    }

    function loadBlocksForRange(startDate, endDate) {
        var start = startOfDay(startDate).toISOString();
        var end = addDays(startOfDay(endDate), 1).toISOString();
        return apiFetch("/api/schedule-blocks?start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end))
            .then(function(data) {
                blocks = (data.blocks || []).map(enrichBlockBoardColor);
            })
            .catch(function(err) {
                blocks = [];
                if (typeof showSnackbar === "function") {
                    showSnackbar((err && err.message) || "Could not load schedule blocks", "error");
                }
            });
    }

    function refreshAll() {
        if (typeof onChanged === "function") return onChanged().then(refreshTimerStatus);
        return refreshTimerStatus();
    }

    function repaintScheduleBlocks() {
        if (!gridRoot) return;
        if (gridMode === "month") {
            paintMonthBlocks(gridRoot);
            paintMonthHours(gridRoot);
            scheduleMonthCalendarResize(gridRoot);
            return;
        }
        if (gridPeriodStart != null && gridDayCount > 0) {
            paintBlocks(gridRoot, gridPeriodStart, gridDayCount);
            updateHoursSummary(gridRoot, gridPeriodStart, gridDayCount, gridMode || "week");
        }
    }

    function reloadScheduleBlocks() {
        if (!visibleRangeStart || !visibleRangeEnd) return refreshTimerStatus();
        return loadBlocksForRange(visibleRangeStart, visibleRangeEnd).then(function() {
            repaintScheduleBlocks();
            return refreshTimerStatus();
        });
    }

    function persistBlockTimes(blockId, startDate, endDate, previousStartAt, previousEndAt, successMessage) {
        var block = blocks.filter(function(b) { return b.id === blockId; })[0];
        var startIso = toIsoLocal(startDate);
        var endIso = toIsoLocal(endDate);
        if (block) {
            block.start_at = startIso;
            block.end_at = endIso;
        }
        repaintScheduleBlocks();
        return apiFetch("/api/schedule-blocks/" + encodeURIComponent(blockId), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start_at: startIso, end_at: endIso })
        }).then(function() {
            if (successMessage) showSnackbar(successMessage, "success");
            return refreshTimerStatus();
        }).catch(function(err) {
            if (block) {
                block.start_at = previousStartAt;
                block.end_at = previousEndAt;
            }
            repaintScheduleBlocks();
            showSnackbar(err.message || "Could not update time block", "error");
        });
    }

    function renderBlockHtml(block, dayDate, layoutStyle) {
        var style = layoutStyle || blockStyleForDay(block, dayDate);
        var blockHeight = Math.max(MIN_BLOCK_HEIGHT, style.height || 0);
        var classes = "sched-block";
        if (block.is_timer_running) classes += " is-running";
        if (resolveBlockBoardColor(block)) classes += " has-board-color";
        if (moveState && moveState.blockId === block.id) classes += " is-moving";
        if (resizeState && resizeState.blockId === block.id) classes += " is-resizing";
        if (blockHeight < 30) classes += " is-compact";
        else if (blockHeight < 52) classes += " is-medium";
        var ticket = block.ticket_reference ? block.ticket_reference : "";
        var start = parseBlockDate(block.start_at);
        var end = blockEndDate(block);
        var timeLabel = formatTimeFromMinutes(minutesFromDate(start)) + " - " + formatTimeFromMinutes(minutesFromDate(end));
        var durationLabel = formatBlockDurationMinutes(blockDurationMinutes(block));
        var metaHtml = ticket
            ? '<span class="sched-block-meta">' + escapeAttr(ticket) + "</span>"
            : "";
        var projectKey = blockProjectKey(block);
        var projectAttr = projectKey ? ' data-project-id="' + escapeAttr(projectKey) + '"' : "";
        return '<button type="button" class="' + classes + '" data-id="' + block.id + '"' + projectAttr + ' style="' + blockInlineStyle(block, style) + '" title="' + escapeAttr((block.title || "Block") + " (" + timeLabel + ")") + '">' +
            '<span class="sched-block-header">' +
            '<span class="sched-block-title">' + escapeAttr(block.title || "Block") + "</span>" +
            metaHtml +
            "</span>" +
            '<span class="sched-block-duration">' + escapeAttr(durationLabel) + "</span>" +
            '<span class="sched-block-time">' + escapeAttr(timeLabel) + "</span>" +
            '<span class="sched-block-resize-handle" data-edge="bottom" aria-hidden="true"></span>' +
            '<span class="sched-block-resize-handle is-top" data-edge="top" aria-hidden="true"></span>' +
            "</button>";
    }

    function paintBlocks(root, periodStart, dayCount) {
        columnRefs = [];
        for (var i = 0; i < dayCount; i += 1) {
            var dayDate = addDays(periodStart, i);
            var col = root.querySelector('.automation-cal-week-day-col[data-day-index="' + i + '"] .relative');
            if (!col) continue;
            columnRefs.push({ el: col, dayDate: dayDate, dayIndex: i });
            col.querySelectorAll(".sched-block").forEach(function(node) { node.remove(); });
            var dayBlocks = blocksForDay(dayDate);
            dayBlocks.forEach(function(block) {
                col.insertAdjacentHTML("beforeend", renderBlockHtml(block, dayDate, blockStyleForDay(block, dayDate)));
            });
            applyColumnOverlapLayout(col);
        }
    }

    function onBlockDblClick(e, blockEl) {
        if (runningTimer || suppressBlockClick) return;
        e.preventDefault();
        e.stopPropagation();
        var id = parseInt(blockEl.getAttribute("data-id"), 10);
        var block = blocks.filter(function(b) { return b.id === id; })[0];
        if (!block) return;
        openEditor(block, parseBlockDate(block.start_at), parseBlockDate(block.end_at));
    }

    function onBlockMouseDown(e, blockEl) {
        if (blockCalendarDragWhileTimerRunning() || e.button !== 0) return;
        if (e.target.closest(".sched-block-resize-handle")) return;
        suppressBlockClick = false;
        var id = parseInt(blockEl.getAttribute("data-id"), 10);
        var block = blocks.filter(function(b) { return b.id === id; })[0];
        if (!block) return;
        e.preventDefault();
        e.stopPropagation();
        var col = columnRefs.filter(function(c) {
            return sameDay(c.dayDate, parseBlockDate(block.start_at));
        })[0];
        var blockRect = blockEl.getBoundingClientRect();
        var offsetPx = e.clientY - blockRect.top;
        var rawGrabOffset = (Math.max(0, offsetPx) / getRowHeight()) * SLOT_MINUTES;
        var grabOffset = snapToFifteen
            ? Math.floor(rawGrabOffset / SLOT_MINUTES) * SLOT_MINUTES
            : Math.round(rawGrabOffset);
        var duration = Math.max(interactionIncrement(), blockDurationMinutes(block));
        var startMinute = minutesFromDate(parseBlockDate(block.start_at));
        var clampedStart = clampStartMinute(startMinute, duration);
        grabOffset = Math.max(0, Math.min(grabOffset, Math.max(0, duration - interactionIncrement())));
        moveState = {
            blockId: id,
            duration: duration,
            grabOffset: grabOffset,
            dayIndex: col ? col.dayIndex : 0,
            originDayIndex: col ? col.dayIndex : 0,
            currentMinute: clampedStart,
            originStartMinute: clampedStart
        };
        setInteracting(true);
        paintInteractionPreviews(gridRoot);
    }

    function onResizeMouseDown(e, handleEl) {
        if (blockCalendarDragWhileTimerRunning() || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        suppressBlockClick = true;
        var blockEl = handleEl.closest(".sched-block");
        if (!blockEl) return;
        var id = parseInt(blockEl.getAttribute("data-id"), 10);
        var block = blocks.filter(function(b) { return b.id === id; })[0];
        if (!block) return;
        var col = columnRefs.filter(function(c) {
            return sameDay(c.dayDate, parseBlockDate(block.start_at));
        })[0];
        var startMinute = minutesFromDate(parseBlockDate(block.start_at));
        var endMinute = minutesFromDate(parseBlockDate(block.end_at));
        resizeState = {
            blockId: id,
            edge: handleEl.getAttribute("data-edge") || "bottom",
            dayIndex: col ? col.dayIndex : 0,
            dayDate: parseBlockDate(block.start_at),
            startMinute: startMinute,
            endMinute: endMinute,
            originStartMinute: startMinute,
            originEndMinute: endMinute
        };
        setInteracting(true);
        paintInteractionPreviews(gridRoot);
    }

    function onColumnMouseDown(e) {
        if (blockCalendarDragWhileTimerRunning() || e.button !== 0) return;
        if (e.target.closest(".sched-block, .automation-cal-week-block, .sched-block-resize-handle")) return;
        var col = e.target.closest(".automation-cal-week-day-col");
        if (!col || gridPeriodStart == null) return;
        var surface = col.querySelector(".relative");
        if (!surface) return;
        var dayIndex = parseInt(col.getAttribute("data-day-index"), 10);
        if (isNaN(dayIndex)) return;
        var pointer = getPointerDayAndTime(e.clientX, e.clientY, null);
        var minute = pointer && pointer.dayIndex === dayIndex
            ? pointer.minute
            : snapInteractionMinute(DAY_START + (Math.floor(Math.max(0, e.clientY - surface.getBoundingClientRect().top) / getRowHeight()) * SLOT_MINUTES));
        dragState = {
            dayIndex: dayIndex,
            dayDate: addDays(gridPeriodStart, dayIndex),
            anchorMinute: minute,
            currentMinute: minute
        };
        setInteracting(true);
        paintInteractionPreviews(gridRoot);
        e.preventDefault();
    }

    function ensureGridInteractions(root) {
        if (!root || root._schedGridBound) return;
        root._schedGridBound = true;
        root.addEventListener("mousedown", function(e) {
            var handle = e.target.closest(".sched-block-resize-handle");
            if (handle) {
                onResizeMouseDown(e, handle);
                return;
            }
            var block = e.target.closest(".sched-block");
            if (block) {
                onBlockMouseDown(e, block);
                return;
            }
            onColumnMouseDown(e);
        });
        root.addEventListener("dblclick", function(e) {
            var block = e.target.closest(".sched-block");
            if (block) onBlockDblClick(e, block);
        });
        root.addEventListener("contextmenu", function(e) {
            var block = e.target.closest(".sched-block");
            if (!block || runningTimer) return;
            e.preventDefault();
            e.stopPropagation();
            var id = parseInt(block.getAttribute("data-id"), 10);
            if (!id) return;
            openBlockContextMenu(id, e.clientX, e.clientY);
        });
    }

    function onGlobalMouseMove(e) {
        if (dragState) {
            var pointer = getPointerDayAndTime(e.clientX, e.clientY, "end");
            if (pointer && pointer.dayIndex === dragState.dayIndex) {
                dragState.currentMinute = pointer.minute;
            }
            paintInteractionPreviews(gridRoot);
            return;
        }
        if (moveState) {
            var pointerMove = getPointerDayAndTime(e.clientX, e.clientY, null);
            if (!pointerMove) return;
            var nextStart = snapInteractionMinute(
                clampStartMinute(pointerMove.minute - moveState.grabOffset, moveState.duration)
            );
            if (nextStart !== moveState.currentMinute || pointerMove.dayIndex !== moveState.dayIndex) {
                suppressBlockClick = true;
            }
            moveState.dayIndex = pointerMove.dayIndex;
            moveState.currentMinute = nextStart;
            paintInteractionPreviews(gridRoot);
            return;
        }
        if (resizeState) {
            var pointerResize = getPointerDayAndTime(e.clientX, e.clientY, resizeState.edge === "bottom" ? "end" : null);
            if (!pointerResize) return;
            var inc = interactionIncrement();
            if (resizeState.edge === "top") {
                resizeState.startMinute = snapInteractionMinute(
                    Math.max(DAY_START, Math.min(pointerResize.minute, resizeState.endMinute - inc))
                );
            } else {
                resizeState.endMinute = snapInteractionMinute(
                    Math.min(DAY_END, Math.max(pointerResize.minute, resizeState.startMinute + inc))
                );
            }
            suppressBlockClick = true;
            paintInteractionPreviews(gridRoot);
        }
    }

    function onGlobalMouseUp() {
        if (resizeState) {
            var finalResize = resizeState;
            clearInteractionPreviews(gridRoot);
            setInteracting(false);
            resizeState = null;
            if (finalResize.startMinute !== finalResize.originStartMinute ||
                finalResize.endMinute !== finalResize.originEndMinute) {
                var resized = blocks.filter(function(b) { return b.id === finalResize.blockId; })[0];
                if (resized) {
                    var resizeStartMinute = Math.min(finalResize.startMinute, finalResize.endMinute - interactionIncrement());
                    var resizeEndMinute = Math.max(finalResize.endMinute, resizeStartMinute + interactionIncrement());
                    var resizeProjectKey = blockProjectKey(resized);
                    if (hasSameProjectTimeOverlap(
                        finalResize.blockId,
                        finalResize.dayDate,
                        resizeStartMinute,
                        resizeEndMinute,
                        resizeProjectKey
                    )) {
                        showSnackbar(sameProjectOverlapMessage(), "warning");
                    } else {
                        var startDt = dateWithMinutes(finalResize.dayDate, resizeStartMinute);
                        var endDt = dateWithMinutes(finalResize.dayDate, resizeEndMinute);
                        persistBlockTimes(
                            finalResize.blockId,
                            startDt,
                            endDt,
                            resized.start_at,
                            resized.end_at,
                            "Time block updated"
                        );
                    }
                }
            }
            window.setTimeout(function() { suppressBlockClick = false; }, 0);
            return;
        }
        if (moveState) {
            var finalMove = moveState;
            clearInteractionPreviews(gridRoot);
            setInteracting(false);
            moveState = null;
            if (finalMove.currentMinute !== finalMove.originStartMinute ||
                finalMove.dayIndex !== finalMove.originDayIndex) {
                var moved = blocks.filter(function(b) { return b.id === finalMove.blockId; })[0];
                var moveCol = columnRefs[finalMove.dayIndex];
                if (moved && moveCol) {
                    var moveStartMinute = finalMove.currentMinute;
                    var moveEndMinute = finalMove.currentMinute + finalMove.duration;
                    var moveProjectKey = blockProjectKey(moved);
                    if (hasSameProjectTimeOverlap(
                        finalMove.blockId,
                        moveCol.dayDate,
                        moveStartMinute,
                        moveEndMinute,
                        moveProjectKey
                    )) {
                        showSnackbar(sameProjectOverlapMessage(), "warning");
                    } else {
                        var moveStart = dateWithMinutes(moveCol.dayDate, moveStartMinute);
                        var moveEnd = dateWithMinutes(moveCol.dayDate, moveEndMinute);
                        persistBlockTimes(
                            finalMove.blockId,
                            moveStart,
                            moveEnd,
                            moved.start_at,
                            moved.end_at,
                            "Time block moved"
                        );
                    }
                }
            }
            window.setTimeout(function() { suppressBlockClick = false; }, 0);
            return;
        }
        if (dragState) {
            var startMinute = Math.min(dragState.anchorMinute, dragState.currentMinute);
            var endMinute = Math.min(DAY_END, Math.max(dragState.anchorMinute, dragState.currentMinute) + interactionIncrement());
            if (endMinute <= startMinute) endMinute = startMinute + interactionIncrement();
            clearInteractionPreviews(gridRoot);
            setInteracting(false);
            openEditor(null, dateWithMinutes(dragState.dayDate, startMinute), dateWithMinutes(dragState.dayDate, endMinute));
            dragState = null;
        }
    }

    function parseDateKey(value) {
        var parts = String(value || "").split("-");
        if (parts.length !== 3) return new Date();
        return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    }

    function monthDayContentHost(cell) {
        return cell.querySelector(".automation-cal-day-stack") || cell;
    }

    function measureMonthDayCellContent(cell) {
        if (!cell) return 0;
        var style = window.getComputedStyle(cell);
        var total = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        var gap = parseFloat(style.gap) || 0;
        var children = Array.prototype.slice.call(cell.children);
        children.forEach(function(child, index) {
            if (index > 0) total += gap;
            var childStyle = window.getComputedStyle(child);
            total += child.offsetHeight;
            total += (parseFloat(childStyle.marginTop) || 0) + (parseFloat(childStyle.marginBottom) || 0);
        });
        return total;
    }

    function syncMonthCalendarRowHeights(root) {
        var shell = root && root.querySelector(".automation-cal-month-shell");
        var grid = root && root.querySelector(".automation-cal-month-grid");
        if (!shell || !grid) return;
        var cells = Array.prototype.slice.call(grid.querySelectorAll(".automation-cal-day"));
        if (!cells.length) return;

        var rowCount = Math.ceil(cells.length / 7);
        var weekdays = shell.querySelector(".automation-cal-weekdays");
        var shellStyle = window.getComputedStyle(shell);
        var shellGap = parseFloat(shellStyle.gap) || 0;
        var weekdaysHeight = weekdays ? weekdays.offsetHeight : 0;
        var shellHeight = shell.clientHeight || (root && root.clientHeight) || 0;
        var gridAvailable = Math.max(0, shellHeight - weekdaysHeight - shellGap);

        var maxCellContent = MONTH_DAY_MIN_HEIGHT;
        cells.forEach(function(cell) {
            maxCellContent = Math.max(maxCellContent, Math.ceil(measureMonthDayCellContent(cell)));
        });

        var gridGap = parseFloat(window.getComputedStyle(grid).gap) || 0;
        var rowGaps = Math.max(0, rowCount - 1) * gridGap;
        var heightForRows = Math.max(0, gridAvailable - rowGaps);
        var uniformRowHeight = rowCount > 0 ? heightForRows / rowCount : maxCellContent;
        if (uniformRowHeight < maxCellContent) {
            uniformRowHeight = maxCellContent;
        }

        grid.style.setProperty("--cal-month-rows", String(rowCount));
        grid.style.height = heightForRows > 0 ? heightForRows + "px" : "";
        grid.style.gridTemplateRows = "repeat(" + rowCount + ", minmax(" +
            Math.max(MONTH_DAY_MIN_HEIGHT, Math.ceil(maxCellContent)) + "px, 1fr))";
        cells.forEach(function(cell) {
            cell.style.minHeight = "";
            cell.style.height = "100%";
        });
    }

    function scheduleMonthCalendarResize(root) {
        if (!root) return;
        if (monthResizeFrame) window.cancelAnimationFrame(monthResizeFrame);
        monthResizeFrame = window.requestAnimationFrame(function() {
            monthResizeFrame = window.requestAnimationFrame(function() {
                monthResizeFrame = null;
                syncMonthCalendarRowHeights(root);
            });
        });
    }

    function bindMonthResize(root) {
        if (monthResizeObserver) {
            monthResizeObserver.disconnect();
            monthResizeObserver = null;
        }
        if (typeof ResizeObserver !== "undefined") {
            monthResizeObserver = new ResizeObserver(function() {
                if (gridMode === "month" && gridRoot) scheduleMonthCalendarResize(gridRoot);
            });
            monthResizeObserver.observe(root);
            var shell = root.querySelector(".automation-cal-month-shell");
            if (shell) monthResizeObserver.observe(shell);
        }
        if (monthResizeBound) return;
        monthResizeBound = true;
        window.addEventListener("resize", function() {
            if (gridMode === "month" && gridRoot) scheduleMonthCalendarResize(gridRoot);
        });
    }

    function bindMonthCells(root) {
        root.querySelectorAll(".automation-cal-day").forEach(function(cell) {
            cell.addEventListener("dblclick", function() {
                if (runningTimer) return;
                var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
                openEditor(null, dateWithMinutes(dayDate, 9 * 60), dateWithMinutes(dayDate, 10 * 60));
            });
        });
    }

    function renderMonthBlockHtml(block) {
        var start = parseBlockDate(block.start_at);
        var end = blockEndDate(block);
        var surface = blockSurfaceStyle(block);
        var ticket = block.ticket_reference ? block.ticket_reference : "";
        var titleText = block.title || "Block";
        if (ticket) titleText = titleText + " · " + ticket;
        return '<button type="button" class="sched-block-month' + (resolveBlockBoardColor(block) ? " has-board-color" : "") + '" data-id="' + block.id + '" style="background:' + surface.background + ";color:" + surface.color + ";border:" + surface.border + ';" title="' + escapeAttr(titleText + " (" + formatTimeFromMinutes(minutesFromDate(start)) + " - " + formatTimeFromMinutes(minutesFromDate(end)) + ")") + '">' +
            '<span class="sched-block-month-title">' + escapeAttr(titleText) + "</span>" +
            '<span class="sched-block-month-time">' + escapeAttr(formatTimeFromMinutes(minutesFromDate(start)) + " - " + formatTimeFromMinutes(minutesFromDate(end))) + "</span>" +
            "</button>";
    }

    function paintMonthBlocks(root) {
        root.querySelectorAll(".sched-block-month").forEach(function(node) { node.remove(); });
        root.querySelectorAll(".automation-cal-day").forEach(function(cell) {
            var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
            if (isNaN(dayDate.getTime())) return;
            var host = monthDayContentHost(cell);
            blocksForDay(dayDate).forEach(function(block) {
                host.insertAdjacentHTML("beforeend", renderMonthBlockHtml(block));
            });
        });
        root.querySelectorAll(".sched-block-month").forEach(function(btn) {
            btn.addEventListener("dblclick", function(e) {
                e.preventDefault();
                e.stopPropagation();
                if (runningTimer) return;
                var id = parseInt(btn.getAttribute("data-id"), 10);
                var block = blocks.filter(function(b) { return b.id === id; })[0];
                if (block) openEditor(block, parseBlockDate(block.start_at), parseBlockDate(block.end_at));
            });
            btn.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                e.stopPropagation();
                var id = parseInt(btn.getAttribute("data-id"), 10);
                if (id) openBlockContextMenu(id, e.clientX, e.clientY);
            });
        });
        scheduleMonthCalendarResize(root);
    }

    window.AutomationCalendarBlocks = {
        init: function(opts) {
            apiFetch = opts.apiFetch;
            if (typeof opts.getRowHeight === "function") {
                getRowHeight = opts.getRowHeight;
            }
            showSnackbar = opts.showSnackbar || function() {};
            onChanged = opts.onChanged || null;
            snapToFifteen = opts.snapToFifteen !== false;
            refreshBoardSelectUi();
            loadBoards();
            document.addEventListener("mousemove", onGlobalMouseMove);
            document.addEventListener("mouseup", onGlobalMouseUp);
            var saveBtn = document.getElementById("sched-block-save");
            var cancelBtn = document.getElementById("sched-block-cancel");
            var optionsBtn = document.getElementById("sched-block-options-btn");
            var actionsMenu = document.getElementById("sched-block-actions-menu");
            var actionNaturalizeBtn = document.getElementById("sched-block-action-naturalize");
            var actionDeleteBtn = document.getElementById("sched-block-action-delete");
            var contextMenu = document.getElementById("sched-block-context-menu");
            var timerBtn = document.getElementById("sched-block-timer-btn");
            var boardSelect = document.getElementById("sched-block-board");
            var snapToggle = document.getElementById("sched-block-snap");
            if (saveBtn) saveBtn.addEventListener("click", saveEditor);
            if (cancelBtn) cancelBtn.addEventListener("click", closeEditor);
            if (optionsBtn) {
                optionsBtn.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var open = actionsMenu && actionsMenu.classList.contains("hidden");
                    setEditorActionsOpen(!!open);
                });
            }
            if (actionNaturalizeBtn) {
                actionNaturalizeBtn.addEventListener("click", function() {
                    setEditorActionsOpen(false);
                    naturalizeEditorBlock();
                });
            }
            if (actionDeleteBtn) {
                actionDeleteBtn.addEventListener("click", function() {
                    setEditorActionsOpen(false);
                    deleteEditorBlock();
                });
            }
            if (contextMenu) {
                contextMenu.addEventListener("click", function(e) {
                    var btn = e.target.closest("[data-action]");
                    if (!btn || !blockContextMenuState) return;
                    var blockId = blockContextMenuState.blockId;
                    var action = btn.getAttribute("data-action");
                    closeBlockContextMenu();
                    if (action === "edit") {
                        var block = blocks.filter(function(b) { return b.id === blockId; })[0];
                        if (block) openEditor(block, parseBlockDate(block.start_at), parseBlockDate(block.end_at));
                        return;
                    }
                    if (action === "naturalize") {
                        naturalizeBlockById(blockId, false);
                        return;
                    }
                    if (action === "delete") {
                        deleteBlockById(blockId, false);
                    }
                });
            }
            document.addEventListener("click", function() {
                setEditorActionsOpen(false);
                closeBlockContextMenu();
            });
            document.addEventListener("keydown", function(e) {
                if (e.key === "Escape") {
                    setEditorActionsOpen(false);
                    closeBlockContextMenu();
                }
            });
            var modalPanel = document.querySelector("#sched-block-modal > div");
            if (modalPanel) {
                modalPanel.addEventListener("click", function(e) {
                    e.stopPropagation();
                });
            }
            if (contextMenu) {
                contextMenu.addEventListener("contextmenu", function(e) {
                    e.preventDefault();
                });
            }
            if (timerBtn) timerBtn.addEventListener("click", toggleTimer);
            if (boardSelect) boardSelect.addEventListener("change", function() { loadTicketsForBoard(boardSelect.value); });
            if (snapToggle) snapToggle.addEventListener("change", function() { snapToFifteen = !!snapToggle.checked; });
            refreshTimerStatus();
            setInterval(refreshTimerStatus, 15000);
        },
        loadForVisibleRange: function(mode, anchorDate) {
            var start = startOfDay(anchorDate);
            var end = start;
            if (mode === "week") {
                start = addDays(startOfDay(anchorDate), -startOfDay(anchorDate).getDay());
                end = addDays(start, 6);
            } else if (mode === "month") {
                start = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), 1);
                end = new Date(anchorDate.getFullYear(), anchorDate.getMonth() + 1, 0);
            }
            return loadBlocksForRange(start, end).then(refreshTimerStatus);
        },
        afterTimeGridRender: function(root, periodStart, dayCount) {
            gridRoot = root;
            gridPeriodStart = periodStart;
            gridDayCount = dayCount;
            gridMode = dayCount === 1 ? "day" : "week";
            visibleRangeStart = periodStart;
            visibleRangeEnd = addDays(periodStart, Math.max(0, dayCount - 1));
            root.querySelectorAll(".automation-cal-week-day-col").forEach(function(col, index) {
                col.setAttribute("data-day-index", String(index));
            });
            ensureGridInteractions(root);
            paintBlocks(root, periodStart, dayCount);
            var mode = dayCount === 1 ? "day" : "week";
            updateHoursSummary(root, periodStart, dayCount, mode);
        },
        afterMonthRender: function(root) {
            gridRoot = root;
            gridMode = "month";
            gridDayCount = 0;
            gridPeriodStart = null;
            var monthDates = [];
            root.querySelectorAll(".automation-cal-day[data-date-key]").forEach(function(cell) {
                var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
                if (!isNaN(dayDate.getTime())) monthDates.push(dayDate);
            });
            if (monthDates.length) {
                monthDates.sort(function(a, b) { return a.getTime() - b.getTime(); });
                visibleRangeStart = monthDates[0];
                visibleRangeEnd = monthDates[monthDates.length - 1];
            }
            bindMonthResize(root);
            paintMonthBlocks(root);
            paintMonthHours(root);
            bindMonthCells(root);
            scheduleMonthCalendarResize(root);
            var monthTotalSeconds = 0;
            root.querySelectorAll(".automation-cal-day[data-date-key]").forEach(function(cell) {
                var dayDate = parseDateKey(cell.getAttribute("data-date-key"));
                if (!isNaN(dayDate.getTime())) monthTotalSeconds += totalSecondsForDay(dayDate);
            });
            var totalEl = document.getElementById("sched-block-hours-total-value");
            var totalLabel = document.getElementById("sched-block-hours-total-label");
            if (totalEl) totalEl.textContent = formatSecondsDuration(monthTotalSeconds);
            if (totalLabel) totalLabel.textContent = "Month total";
        },
        getRunningTimer: function() { return runningTimer; },
        getVisibleExportRange: function() {
            if (!visibleRangeStart || !visibleRangeEnd) return null;
            return {
                start_date: toDateOnly(visibleRangeStart),
                end_date: toDateOnly(visibleRangeEnd),
            };
        }
    };
})();
