import React from 'react';
import { useTickets } from '../hooks/useTickets';

export function TicketBoard({ boardId }) {
  const { tickets, loading, reload, includeArchived, setIncludeArchived } = useTickets(boardId);
  return (
    <section aria-busy={loading}>
      <button type="button" onClick={reload}>Refresh</button>
      <label>
        <input type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} />
        Show archived
      </label>
      <p>{tickets.length} tickets</p>
      <ul>{tickets.map(ticket => <li key={ticket.id}>{ticket.title}</li>)}</ul>
    </section>
  );
}
