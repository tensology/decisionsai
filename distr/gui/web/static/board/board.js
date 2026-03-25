// Board state
let boardData = null;
let isLocalProvider = true;
let projectId = null;

// Get project_id from URL query params
function getProjectId() {
    if (projectId) return projectId;
    const urlParams = new URLSearchParams(window.location.search);
    projectId = urlParams.get('project_id');
    return projectId;
}

// Initialize board on page load
document.addEventListener('DOMContentLoaded', () => {
    try {
        console.log('Board JS loaded, initializing...');
        
        // Check if SortableJS is loaded
        if (typeof Sortable === 'undefined' || !Sortable.create) {
            console.warn('SortableJS not loaded! Trying to load from CDN...');
            // Try loading SortableJS manually
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/sortablejs@latest/Sortable.min.js';
            script.onload = () => {
                console.log('SortableJS loaded successfully');
                initializeBoard();
            };
            script.onerror = () => {
                console.error('Failed to load SortableJS from CDN');
                initializeBoard(); // Continue anyway
            };
            document.head.appendChild(script);
        } else {
            console.log('SortableJS is loaded');
            initializeBoard();
        }
    } catch (error) {
        console.error('Error initializing board:', error);
        const content = document.getElementById('boardContent');
        if (content) {
            content.innerHTML = '<div class="loading" style="color: red;">Error loading board: ' + error.message + '</div>';
        }
    }
});

function initializeBoard() {
    try {
        getProjectId();
        console.log('Project ID:', projectId);
        if (!projectId) {
            const content = document.getElementById('boardContent');
            if (content) {
                content.innerHTML = '<div class="loading">No project selected. Please select a project.</div>';
            }
            return;
        }
        loadBoard();
        setupEventListeners();
    } catch (error) {
        console.error('Error in initializeBoard:', error);
        const content = document.getElementById('boardContent');
        if (content) {
            content.innerHTML = '<div class="loading" style="color: red;">Error: ' + error.message + '</div>';
        }
    }
}

// Setup event listeners
function setupEventListeners() {
    document.getElementById('refreshBtn').addEventListener('click', loadBoard);
    document.getElementById('syncBtn').addEventListener('click', syncBoard);
    document.getElementById('addColumnBtn').addEventListener('click', showAddColumnDialog);

    // Modal controls
    const modal = document.getElementById('ticketModal');
    const closeBtn = modal.querySelector('.close');
    const cancelBtn = modal.querySelector('.cancel-btn');

    closeBtn.addEventListener('click', closeModal);
    cancelBtn.addEventListener('click', closeModal);

    // Close modal when clicking outside
    window.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal();
        }
    });

    // Form submission
    document.getElementById('ticketForm').addEventListener('submit', handleTicketSubmit);
    document.getElementById('deleteTicketBtn').addEventListener('click', handleTicketDelete);
}

// API calls - using XMLHttpRequest for better QWebEngineView compatibility
function apiCall(endpoint, method = 'GET', data = null) {
    return new Promise((resolve, reject) => {
        // Get base path from current URL (handles both /board/ and standalone / paths)
        const basePath = window.location.pathname.replace(/\/$/, '').replace(/\/\?.*$/, '') || '';
        
        // Remove leading slash from endpoint and append to base path
        const cleanEndpoint = endpoint.startsWith('/') ? endpoint.substring(1) : endpoint;
        const fullEndpoint = `${basePath}/${cleanEndpoint}`;
        
        // Append project_id to endpoint
        const separator = fullEndpoint.includes('?') ? '&' : '?';
        const urlWithProject = `${fullEndpoint}${separator}project_id=${projectId}`;

        console.log('API Request:', method, urlWithProject);

        const xhr = new XMLHttpRequest();
        xhr.open(method, urlWithProject, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.timeout = 10000; // 10 second timeout

        xhr.onload = function() {
            console.log('API Response status:', xhr.status);
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const result = JSON.parse(xhr.responseText);
                    console.log('API Response parsed successfully');
                    resolve(result);
                } catch (e) {
                    console.error('JSON parse error:', e);
                    reject(new Error('Invalid JSON response'));
                }
            } else {
                try {
                    const error = JSON.parse(xhr.responseText);
                    reject(new Error(error.detail || 'Request failed'));
                } catch (e) {
                    reject(new Error('Request failed with status ' + xhr.status));
                }
            }
        };

        xhr.onerror = function() {
            console.error('XHR Network error');
            reject(new Error('Network error'));
        };

        xhr.ontimeout = function() {
            console.error('XHR Timeout');
            reject(new Error('Request timed out'));
        };

        if (data) {
            xhr.send(JSON.stringify(data));
        } else {
            xhr.send();
        }
    });
}

// Load board from API
async function loadBoard() {
    console.log('Loading board...');
    console.log('Current location:', window.location.href);
    console.log('Project ID:', projectId);
    
    const boardContent = document.getElementById('boardContent');
    if (!boardContent) {
        console.error('boardContent element not found!');
        return;
    }
    boardContent.innerHTML = '<div class="loading">Fetching board data...</div>';
    
    try {
        console.log('Calling API...');
        boardData = await apiCall('/api/board');
        console.log('Board data received:', JSON.stringify(boardData).substring(0, 200));
        console.log('Columns count:', boardData?.columns?.length || 0);
        isLocalProvider = boardData.provider === 'local';

        // Update page title and header with project name
        if (boardData.project_name) {
            document.title = boardData.project_name;
            const titleElement = document.querySelector('.board-title');
            if (titleElement) {
                titleElement.textContent = boardData.project_name;
            }
        }

        // Show/hide buttons based on provider
        document.getElementById('syncBtn').style.display = isLocalProvider ? 'none' : 'inline-flex';
        document.getElementById('addColumnBtn').style.display = isLocalProvider ? 'inline-flex' : 'none';

        console.log('Rendering board...');
        renderBoard();
        console.log('Board rendered successfully');
        showSnackbar('Board refreshed');
    } catch (error) {
        console.error('Failed to load board:', error);
        console.error('Error stack:', error.stack);
        boardContent.innerHTML = `<div class="loading" style="color: #ff6b6b;">Failed to load board: ${error.message}</div>`;
        showSnackbar('Failed to refresh board', true);
    }
}

// Sync board from Trello
async function syncBoard() {
    try {
        showSnackbar('Syncing from Trello...');
        boardData = await apiCall('/api/board/sync', 'POST');
        renderBoard();
        showSnackbar('Board synced from Trello successfully');
    } catch (error) {
        console.error('Failed to sync board:', error);
        showSnackbar('Failed to sync from Trello', true);
    }
}

// Render board UI
function renderBoard() {
    if (!boardData || !boardData.columns) {
        document.getElementById('boardContent').innerHTML =
            '<div class="loading">No board data available</div>';
        return;
    }

    const columnsHtml = boardData.columns.map(column => createColumnHtml(column)).join('');

    document.getElementById('boardContent').innerHTML = `
        <div class="board-columns" id="boardColumns">
            ${columnsHtml}
        </div>
    `;

    // Initialize drag and drop for tickets in all columns
    boardData.columns.forEach(column => {
        initializeDragAndDrop(column.id);
    });

    // Initialize drag and drop for columns
    initializeColumnDragAndDrop();

    // Update assignee datalist
    updateAssigneeDatalist();
}

// Update assignee datalist from existing tickets
function updateAssigneeDatalist() {
    if (!boardData || !boardData.columns) return;

    // Collect unique assignees from all tickets
    const assignees = new Set();
    boardData.columns.forEach(column => {
        column.tickets.forEach(ticket => {
            if (ticket.assignee && ticket.assignee.trim()) {
                // Split by comma for multiple assignees
                const names = ticket.assignee.split(',').map(name => name.trim());
                names.forEach(name => {
                    if (name) assignees.add(name);
                });
            }
        });
    });

    // Update datalist
    const datalist = document.getElementById('assigneeList');
    if (datalist) {
        datalist.innerHTML = Array.from(assignees)
            .sort()
            .map(assignee => `<option value="${escapeHtml(assignee)}">`)
            .join('');
    }
}

// Update column ticket counts in UI
function updateColumnCounts() {
    if (!boardData || !boardData.columns) return;

    boardData.columns.forEach(column => {
        const countElement = document.querySelector(`.board-column[data-column-id="${column.id}"] .column-count`);
        if (countElement) {
            countElement.textContent = column.tickets.length;
        }
    });
}

// Create HTML for a column
function createColumnHtml(column) {
    const ticketsHtml = column.tickets.map(ticket => createTicketHtml(ticket)).join('');

    return `
        <div class="board-column" data-column-id="${column.id}" data-column-position="${column.position || 0}">
            <div class="column-header" title="Drag to reorder column">
                <div class="column-title">
                    <span class="column-drag-icon" title="Drag to reorder">☰</span>
                    ${escapeHtml(column.name)}
                    <span class="column-count">${column.tickets.length}</span>
                </div>
                ${isLocalProvider ? `<button class="column-menu-btn" onclick="showColumnMenu(event, '${column.id}')" title="Column menu">⋮</button>` : ''}
            </div>
            <div class="column-tickets" data-column-id="${column.id}">
                ${ticketsHtml}
            </div>
            <button class="add-ticket-btn" onclick="showAddTicketModal('${column.id}')">
                + Add Ticket
            </button>
        </div>
    `;
}

// Create HTML for a ticket
function createTicketHtml(ticket) {
    const tags = ticket.tags && ticket.tags.length > 0
        ? `<div class="ticket-tags">${ticket.tags.map(tag => `<span class="ticket-tag">${escapeHtml(tag)}</span>`).join('')}</div>`
        : '';

    const assignee = ticket.assignee
        ? `<div class="ticket-meta-item">👤 ${escapeHtml(ticket.assignee)}</div>`
        : '';

    const dueDate = ticket.due_date
        ? `<div class="ticket-meta-item">📅 ${formatDate(ticket.due_date)}</div>`
        : '';

    const priority = ticket.priority
        ? `<div class="ticket-meta-item priority-${ticket.priority}">⚠ ${ticket.priority.toUpperCase()}</div>`
        : '';

    const timeEstimate = ticket.time_estimate
        ? `<div class="ticket-meta-item">⏱ ${escapeHtml(ticket.time_estimate)}</div>`
        : '';

    // Add priority-based border class
    const priorityClass = ticket.priority ? `ticket-priority-${ticket.priority}` : '';
    
    return `
        <div class="ticket-card ${priorityClass}" data-ticket-id="${ticket.id}" data-priority="${ticket.priority || ''}" ondblclick="showEditTicketModal('${ticket.id}')" oncontextmenu="event.preventDefault(); showTicketContextMenu(event, '${ticket.id}')">
            <div class="ticket-actions">
                <div class="ticket-edit-icon" data-ticket-id="${ticket.id}" onclick="event.stopPropagation(); showEditTicketModal('${ticket.id}')" title="Edit ticket">✏️</div>
                <div class="ticket-send-icon" data-ticket-id="${ticket.id}" onclick="event.stopPropagation(); sendTicketToProject('${ticket.id}')" title="Send to project">📤</div>
            </div>
            <div class="ticket-title">${escapeHtml(ticket.title)}</div>
            <div class="ticket-meta">
                ${assignee}
                ${dueDate}
                ${priority}
                ${timeEstimate}
            </div>
            ${tags}
        </div>
    `;
}

// Initialize drag and drop for tickets in a column
function initializeDragAndDrop(columnId) {
    const columnElement = document.querySelector(`.column-tickets[data-column-id="${columnId}"]`);
    if (!columnElement) return;

    Sortable.create(columnElement, {
        group: 'tickets',
        animation: 200,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        easing: "cubic-bezier(1, 0, 0, 1)",
        delay: 100,
        delayOnTouchOnly: true,
        touchStartThreshold: 3,
        forceFallback: false,
        handle: '.ticket-card', // Only allow dragging by the card itself, not the edit icon

        onEnd: async function (evt) {
            const ticketId = evt.item.dataset.ticketId;
            const newColumnId = evt.to.dataset.columnId;
            const newPosition = evt.newIndex;

            // Call API to move ticket in the background
            try {
                await apiCall(`/api/tickets/${ticketId}/move`, 'POST', {
                    new_column_id: newColumnId,
                    position: newPosition
                });

                // Update local board data without reloading UI
                // Find and remove ticket from old column
                let movedTicket = null;
                for (const column of boardData.columns) {
                    const ticketIndex = column.tickets.findIndex(t => String(t.id) === String(ticketId));
                    if (ticketIndex !== -1) {
                        movedTicket = column.tickets.splice(ticketIndex, 1)[0];
                        break;
                    }
                }

                // Add ticket to new column at new position
                if (movedTicket) {
                    const newColumn = boardData.columns.find(c => String(c.id) === String(newColumnId));
                    if (newColumn) {
                        movedTicket.column_id = newColumnId;
                        movedTicket.position = newPosition;
                        newColumn.tickets.splice(newPosition, 0, movedTicket);

                        // Update column counts in UI
                        updateColumnCounts();
                    }
                }
            } catch (error) {
                console.error('Failed to move ticket:', error);
                // Reload board to revert UI changes on error
                await loadBoard();
            }
        }
    });
}

// Initialize drag and drop for columns
function initializeColumnDragAndDrop() {
    const columnsContainer = document.getElementById('boardColumns');
    if (!columnsContainer || !isLocalProvider) return;

    // Check if SortableJS is loaded
    if (typeof Sortable === 'undefined' || !Sortable.create) {
        console.error('SortableJS not loaded! Column dragging disabled.');
        return;
    }

    console.log('Initializing column drag and drop...');

    Sortable.create(columnsContainer, {
        animation: 150,
        ghostClass: 'column-sortable-ghost',
        dragClass: 'column-sortable-drag',
        chosenClass: 'column-chosen',
        handle: '.column-drag-icon, .column-header',
        draggable: '.board-column',
        filter: function(evt, target) {
            // Don't start drag if clicking on menu button, add ticket button, or tickets
            const menuBtn = target.closest('.column-menu-btn');
            const addBtn = target.closest('.add-ticket-btn');
            const ticket = target.closest('.ticket-card');
            const columnTickets = target.closest('.column-tickets');
            
            if (menuBtn || addBtn || ticket || (columnTickets && !target.closest('.column-header'))) {
                console.log('Drag prevented: clicked on interactive element');
                return true; // Return true to prevent drag
            }
            return false; // Return false to allow drag
        },
        preventOnFilter: true,
        delay: 0,
        delayOnTouchOnly: false,
        touchStartThreshold: 0,
        swapThreshold: 0.65,
        invertSwap: false,
        direction: 'horizontal',

        onStart: function(evt) {
            // Add dragging class to the column being dragged
            evt.item.classList.add('column-dragging');
            console.log('Started dragging column:', evt.item.dataset.columnId);
        },

        onMove: function(evt) {
            // Allow movement
            return true;
        },

        onEnd: async function (evt) {
            // Remove dragging class
            evt.item.classList.remove('column-dragging');

            const columnId = evt.item.dataset.columnId;
            const oldPosition = evt.oldIndex;
            const newPosition = evt.newIndex;

            console.log('Dropped column:', columnId, 'from position:', oldPosition, 'to:', newPosition);

            // Only call API if position actually changed
            if (oldPosition === newPosition) {
                console.log('Position unchanged, skipping API call');
                return;
            }

            // Call API to reorder column in the background
            try {
                await apiCall(`/api/columns/${columnId}/reorder`, 'POST', {
                    new_position: newPosition
                });

                // Update local board data without reloading UI
                // The DOM is already updated by SortableJS, just update our data model
                const movedColumn = boardData.columns.splice(oldPosition, 1)[0];
                boardData.columns.splice(newPosition, 0, movedColumn);

                // Update position values in the data model
                boardData.columns.forEach((col, idx) => {
                    col.position = idx;
                });
            } catch (error) {
                console.error('Failed to reorder column:', error);
                // Reload board to revert UI changes on error
                await loadBoard();
            }
        }
    });

    // Add right-click context menu to columns using event delegation
    // This survives re-renders since it's attached to document
    document.addEventListener('contextmenu', function(e) {
        const column = e.target.closest('.board-column');
        if (!column) return;
        
        // Don't show context menu if clicking on menu button or ticket (they have their own menus)
        if (e.target.closest('.column-menu-btn') || e.target.closest('.ticket-card')) {
            return;
        }

        e.preventDefault();
        const columnId = column.dataset.columnId;
        showColumnContextMenu(e, columnId);
        e.stopPropagation();
    }, true); // Use capture phase
}

// Show add ticket modal
function showAddTicketModal(columnId) {
    const modal = document.getElementById('ticketModal');
    document.getElementById('modalTitle').textContent = 'Create Ticket';
    document.getElementById('ticketId').value = '';
    document.getElementById('columnId').value = columnId;
    document.getElementById('deleteTicketBtn').style.display = 'none';

    // Reset form
    document.getElementById('ticketForm').reset();

    modal.classList.add('active');
}

// Show edit ticket modal
async function showEditTicketModal(ticketId) {
    // Show loading spinner on the edit icon
    const editIcon = document.querySelector(`.ticket-edit-icon[data-ticket-id="${ticketId}"]`);
    let originalIcon = null;
    if (editIcon) {
        originalIcon = editIcon.innerHTML;
        editIcon.innerHTML = '<div class="spinner"></div>';
        editIcon.style.pointerEvents = 'none';
    }

    try {
        const ticket = await apiCall(`/api/tickets/${ticketId}`);

        const modal = document.getElementById('ticketModal');
        document.getElementById('modalTitle').textContent = 'Edit Ticket';
        document.getElementById('ticketId').value = ticket.id;
        document.getElementById('columnId').value = ticket.column_id;
        document.getElementById('deleteTicketBtn').style.display = 'inline-flex';

        // Fill form
        document.getElementById('ticketTitle').value = ticket.title || '';
        document.getElementById('ticketDescription').value = ticket.description || '';
        document.getElementById('ticketAssignee').value = ticket.assignee || '';
        document.getElementById('ticketPriority').value = ticket.priority || '';
        document.getElementById('ticketDueDate').value = ticket.due_date ? ticket.due_date.split('T')[0] : '';
        document.getElementById('ticketTags').value = ticket.tags ? ticket.tags.join(', ') : '';
        document.getElementById('ticketTimeEstimate').value = ticket.time_estimate || '';

        modal.classList.add('active');
    } catch (error) {
        console.error('Failed to load ticket:', error);
        showSnackbar('Failed to load ticket', true);
    } finally {
        // Restore original icon
        if (editIcon && originalIcon) {
            editIcon.innerHTML = originalIcon;
            editIcon.style.pointerEvents = '';
        }
    }
}

// Close modal
function closeModal() {
    const modal = document.getElementById('ticketModal');
    modal.classList.remove('active');
    document.getElementById('ticketForm').reset();
}

// Handle ticket form submission
async function handleTicketSubmit(e) {
    e.preventDefault();

    const ticketId = document.getElementById('ticketId').value;
    const columnId = document.getElementById('columnId').value;
    const title = document.getElementById('ticketTitle').value.trim();
    const description = document.getElementById('ticketDescription').value.trim();
    const assignee = document.getElementById('ticketAssignee').value.trim();
    const priority = document.getElementById('ticketPriority').value;
    const dueDate = document.getElementById('ticketDueDate').value;
    const tagsStr = document.getElementById('ticketTags').value.trim();
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(t => t) : [];
    const timeEstimate = document.getElementById('ticketTimeEstimate').value.trim();

    const ticketData = {
        title,
        description: description || null,
        assignee: assignee || null,
        priority: priority || null,
        due_date: dueDate || null,
        tags: tags.length > 0 ? tags : null,
        time_estimate: timeEstimate || null
    };

    try {
        if (ticketId) {
            // Update existing ticket
            await apiCall(`/api/tickets/${ticketId}`, 'PUT', ticketData);
        } else {
            // Create new ticket
            await apiCall('/api/tickets', 'POST', {
                column_id: columnId,
                ...ticketData
            });
        }

        closeModal();
        await loadBoard();
    } catch (error) {
        console.error('Failed to save ticket:', error);
    }
}

// Handle ticket deletion
async function handleTicketDelete() {
    const ticketId = document.getElementById('ticketId').value;

    if (!confirm('Are you sure you want to delete this ticket?')) {
        return;
    }

    try {
        await apiCall(`/api/tickets/${ticketId}`, 'DELETE');
        closeModal();
        // Remove ticket from DOM without reloading the entire board
        const ticketEl = document.querySelector(`.ticket-card[data-ticket-id="${ticketId}"]`);
        if (ticketEl) {
            ticketEl.remove();
        }
    } catch (error) {
        console.error('Failed to delete ticket:', error);
    }
}

// Show column context menu (from menu button)
function showColumnMenu(event, columnId) {
    event.stopPropagation();
    event.preventDefault();
    showColumnContextMenu(event, columnId);
}

// Show column context menu (internal function)
function showColumnContextMenu(event, columnId) {
    // Remove existing menu
    const existingMenu = document.querySelector('.context-menu');
    if (existingMenu) {
        existingMenu.remove();
    }

    const column = boardData.columns.find(c => String(c.id) === String(columnId));
    if (!column) return;

    const menu = document.createElement('div');
    menu.className = 'context-menu active';
    menu.style.left = event.pageX + 'px';
    menu.style.top = event.pageY + 'px';

    menu.innerHTML = `
        <div class="context-menu-item" onclick="showAddTicketModal('${columnId}'); this.closest('.context-menu').remove();">➕ Add New Ticket</div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" onclick="renameColumn('${columnId}')">Rename Column</div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" onclick="deleteColumn('${columnId}')">Delete Column</div>
    `;

    document.body.appendChild(menu);

    // Close menu when clicking anywhere
    setTimeout(() => {
        function closeMenu() {
            menu.remove();
            document.removeEventListener('click', closeMenu);
            document.removeEventListener('contextmenu', closeMenu);
        }
        document.addEventListener('click', closeMenu);
        document.addEventListener('contextmenu', closeMenu);
    }, 10);
}

// Rename column
async function renameColumn(columnId) {
    const column = boardData.columns.find(c => String(c.id) === String(columnId));
    if (!column) return;

    const newName = prompt('Enter new column name:', column.name);
    if (!newName || newName.trim() === '' || newName === column.name) return;

    try {
        await apiCall(`/api/columns/${columnId}`, 'PUT', { name: newName.trim() });
        await loadBoard();
    } catch (error) {
        console.error('Failed to rename column:', error);
    }
}

// Delete column
async function deleteColumn(columnId) {
    const column = boardData.columns.find(c => String(c.id) === String(columnId));
    if (!column) return;

    const ticketCount = column.tickets.length;
    let message = `Are you sure you want to delete column "${column.name}"?`;
    if (ticketCount > 0) {
        message += `\n\nThis will also delete ${ticketCount} ticket(s) in this column.`;
    }

    if (!confirm(message)) return;

    try {
        await apiCall(`/api/columns/${columnId}`, 'DELETE');
        await loadBoard();
    } catch (error) {
        console.error('Failed to delete column:', error);
    }
}

// Show add column dialog
async function showAddColumnDialog() {
    const columnName = prompt('Enter column name:');
    if (!columnName || columnName.trim() === '') return;

    try {
        await apiCall('/api/columns', 'POST', {
            name: columnName.trim(),
            position: boardData.columns.length
        });
        await loadBoard();
    } catch (error) {
        console.error('Failed to create column:', error);
    }
}

// Utility functions
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// Snackbar notification
function showSnackbar(message, isError = false) {
    window.DecisionsAPI.snackbar(message, isError ? "error" : "success", { id: "board-snackbar" });
}

// Show ticket context menu
function showTicketContextMenu(event, ticketId) {
    event.stopPropagation();
    
    // Remove existing menu
    const existingMenu = document.querySelector('.context-menu');
    if (existingMenu) {
        existingMenu.remove();
    }

    // Create context menu
    const menu = document.createElement('div');
    menu.className = 'context-menu active';
    menu.style.position = 'fixed';
    menu.style.left = event.pageX + 'px';
    menu.style.top = event.pageY + 'px';
    menu.style.zIndex = '10000';

    menu.innerHTML = `
        <div class="context-menu-item" onclick="showEditTicketModal('${ticketId}'); this.closest('.context-menu').remove();">
            ✏️ Edit Ticket
        </div>
        <div class="context-menu-item" onclick="sendTicketToProject('${ticketId}'); this.closest('.context-menu').remove();">
            📤 Send to Project
        </div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" onclick="archiveTicket('${ticketId}'); this.closest('.context-menu').remove();" style="color: #ffa500;">
            📦 Archive Ticket
        </div>
        <div class="context-menu-item" onclick="deleteTicket('${ticketId}'); this.closest('.context-menu').remove();" style="color: #ff6b6b;">
            🗑️ Delete Ticket
        </div>
    `;

    document.body.appendChild(menu);

    // Close menu when clicking anywhere
    setTimeout(() => {
        function closeMenu(e) {
            if (!menu.contains(e.target)) {
                menu.remove();
                document.removeEventListener('click', closeMenu);
                document.removeEventListener('contextmenu', closeMenu);
            }
        }
        document.addEventListener('click', closeMenu);
        document.addEventListener('contextmenu', closeMenu);
    }, 10);
}

// Delete ticket function
async function deleteTicket(ticketId) {
    if (!confirm('Are you sure you want to delete this ticket?')) {
        return;
    }

    try {
        await apiCall(`/api/tickets/${ticketId}`, 'DELETE');
        showSnackbar('Ticket deleted');
        // Remove ticket from DOM without reloading the entire board
        const ticketEl = document.querySelector(`.ticket-card[data-ticket-id="${ticketId}"]`);
        if (ticketEl) {
            ticketEl.remove();
        }
    } catch (error) {
        console.error('Failed to delete ticket:', error);
        showSnackbar('Failed to delete ticket', true);
    }
}

// Send ticket to active project
async function sendTicketToProject(ticketId) {
    try {
        showSnackbar('Sending ticket to project...');
        const result = await apiCall(`/api/tickets/${ticketId}/send-to-project`, 'POST');
        showSnackbar(`Ticket sent to project "${result.project_name}" successfully!`);
    } catch (error) {
        console.error('Failed to send ticket to project:', error);
        const errorMsg = error.message || 'Failed to send ticket to project';
        showSnackbar(errorMsg, true);
    }
}

// Archive ticket
async function archiveTicket(ticketId) {
    try {
        await apiCall(`/api/tickets/${ticketId}/archive`, 'POST');
        showSnackbar('Ticket archived successfully');
        // Remove ticket from DOM without reloading the entire board
        const ticketEl = document.querySelector(`.ticket-card[data-ticket-id="${ticketId}"]`);
        if (ticketEl) {
            ticketEl.remove();
        }
    } catch (error) {
        console.error('Failed to archive ticket:', error);
        showSnackbar('Failed to archive ticket', true);
    }
}

// Disable right-click context menu globally except for specific elements
document.addEventListener('contextmenu', function(e) {
    // Allow right-click on column headers, tickets, menu buttons, and context menus
    if (e.target.closest('.column-header') ||
        e.target.closest('.ticket-card') ||
        e.target.closest('.column-menu-btn') ||
        e.target.closest('.context-menu') ||
        e.target.closest('.board-column')) {
        return; // Allow default behavior (our handlers will preventDefault)
    }
    // Prevent default context menu everywhere else
    e.preventDefault();
});

// Make functions available globally
window.showAddTicketModal = showAddTicketModal;
window.showEditTicketModal = showEditTicketModal;
window.showColumnMenu = showColumnMenu;
window.showTicketContextMenu = showTicketContextMenu;
window.deleteTicket = deleteTicket;
window.renameColumn = renameColumn;
window.deleteColumn = deleteColumn;
window.sendTicketToProject = sendTicketToProject;
window.archiveTicket = archiveTicket;
