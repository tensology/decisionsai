import { useCallback, useEffect, useState } from 'react';
import { fetchBoardTickets } from '../api/tickets';

export function useTickets(boardId) {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await fetchBoardTickets(boardId, { includeArchived });
      setTickets(payload.items);
    } finally {
      setLoading(false);
    }
  }, [boardId, includeArchived]);

  useEffect(() => { reload(); }, [reload]);
  return { tickets, loading, reload, includeArchived, setIncludeArchived };
}
