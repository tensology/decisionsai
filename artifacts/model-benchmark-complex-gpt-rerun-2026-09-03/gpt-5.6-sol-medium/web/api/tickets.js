export async function fetchBoardTickets(boardId, { includeArchived = false } = {}) {
  let url = `/api/boards/${encodeURIComponent(boardId)}/tickets`;
  if (includeArchived) url += '?include_archived=true';

  const response = await fetch(url);
  if (!response.ok) throw new Error(`Ticket request failed: ${response.status}`);
  return response.json();
}
