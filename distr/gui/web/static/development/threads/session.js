// Reads one coherent thread snapshot. Callers commit it only while selection is current.
export function createThreadSession({ api, workflowForChat, runForWorkflow, directExecutionForChat, workflowRunId, isActiveAgentRun, timerSnapshot }) {
    const requests = new Map();
    function cancel() {
        for (const controller of requests.values()) controller.abort();
        requests.clear();
    }
    async function read(chatId, { includeChat = false, previousChat = null, isCurrent = () => true } = {}) {
        const channel = includeChat ? 'open' : 'refresh';
        requests.get(channel)?.abort();
        const controller = new AbortController();
        requests.set(channel, controller);
        const get = (path) => api(path, { signal: controller.signal });
        const current = () => !controller.signal.aborted && isCurrent();
        try {
            const summary = workflowForChat(chatId, previousChat);
            const [chat, linkedWorkflow, artifacts, plans, execution, time] = await Promise.all([
                includeChat ? get(`/agent-conversations/${chatId}?surface=development`) : null,
                summary ? get(`/workflows/${summary.id}`) : null,
                get(`/workflows/studio/tasks/${chatId}/artifacts`),
                get(`/workflows/studio/tasks/${chatId}/plans`),
                get(`/workflows/studio/tasks/${chatId}/execution`).catch(() => null),
                get(`/workflows/studio/tasks/${chatId}/time`).catch(() => null)
            ]);
            if (!current()) return null;
            if (chat && chat.development !== true && (!chat.development || typeof chat.development !== 'object'))
                throw new Error('This conversation belongs to Chat, not Development.');
            const thread = chat || previousChat;
            const events = (thread?.messages || []).filter((message) => message.role === 'workflow')
                .map((message) => message.workflow_event || {});
            const inferred = workflowForChat(chatId, thread);
            const workflow = linkedWorkflow || (inferred ? await get(`/workflows/${inferred.id}`) : null);
            // A workflow definition can be shared by many chats. The task's
            // execution endpoint owns its run identity and terminal state.
            const workflowExecution = execution?.direct === false && workflow
                && Number(execution.workflow_id) === Number(workflow.id)
                && (!execution.chat_id || Number(execution.chat_id) === Number(chatId))
                ? { ...execution, chat_id: Number(chatId) }
                : null;
            let workflowRun = workflowExecution
                ? { ...(runForWorkflow(workflow.id, chatId, workflowRunId(workflowExecution)) || {}), ...workflowExecution }
                : workflow ? runForWorkflow(workflow.id, chatId) : null;
            if (workflow && !workflowRun) {
                const history = await get(`/workflows/${workflow.id}/runs?limit=20&chat_id=${Number(chatId)}`);
                const eventIds = new Set(events.filter((event) => Number(event.workflow_id) === Number(workflow.id))
                    .map((event) => Number(event.run_id)));
                const row = (Array.isArray(history) ? history : history.runs || [])
                    .find((item) => Number(item.chat_id) === Number(chatId) || (item.chat_id == null && eventIds.has(Number(item.id))));
                if (row) workflowRun = { ...row, chat_id: Number(chatId) };
            }
            const run = directExecutionForChat(execution) || workflowRun;
            const query = new URLSearchParams({ chat_id: String(chatId) });
            const runId = workflowRunId(workflowRun);
            if (workflow && runId) query.set('run_id', String(runId));
            const steeringMemory = workflow && runId
                ? await get(`/workflows/${workflow.id}/runs/${runId}/steering-memory`).catch(() => null)
                : null;
            if (workflowRun && steeringMemory) workflowRun = { ...workflowRun, steering_memory: steeringMemory };
            let controls = await get(`/workflows/studio/control-state?${query}`);
            if (!current()) return null;
            if (isActiveAgentRun(run) && (controls.commands || []).some((command) => command.status === 'queued' && command.command_type !== 'followup')) {
                await api(`/workflows/studio/tasks/${chatId}/commands/dispatch`, { method: 'POST', body: {}, signal: controller.signal }).catch(() => null);
                controls = await get(`/workflows/studio/control-state?${query}`);
            }
            if (!current()) return null;
            return {
                ...(chat ? { currentChat: chat } : {}),
                currentWorkflow: workflow,
                currentWorkflowRun: workflowRun,
                currentRun: run?.direct ? run : workflowRun,
                executionAvailable: execution != null,
                lastProactiveResultTurnId: execution?.last_proactive_result_turn_id,
                artifacts: artifacts.items || [],
                plans: plans.items || [],
                steeringMemory,
                controlState: controls,
                threadTime: time ? timerSnapshot(time) : null,
                // Per-turn route Auto/fixed ran on, keyed by user chat_row_id (display only).
                turnRoutes: (execution && typeof execution.turn_routes === 'object' && execution.turn_routes) || {}
            };
        } catch (error) {
            if (!current() || error.name === 'AbortError') return null;
            throw error;
        } finally {
            if (requests.get(channel) === controller) requests.delete(channel);
        }
    }
    return { read, cancel };
}
