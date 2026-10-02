import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api, formatMoney } from "../../lib/api";
import { useAuth } from "../../lib/auth";

type Order = { number: string; total_minor: number; currency: string; payment_state: string; lifecycle_state: string; placed_at: string };

export default function Orders() {
  const { user, loading } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  useEffect(() => {
    if (user) api.get<Order[]>("/api/shop/orders/").then(setOrders);
  }, [user]);
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;

  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-6">Your orders</h1>
      <div className="space-y-3">
        {orders.map((o) => (
          <Link key={o.number} to={`/orders/${o.number}`}
            className="flex justify-between items-center rounded-xl border bg-white p-4 shadow-sm hover:border-primary-200">
            <div>
              <div className="font-medium">{o.number}</div>
              <div className="text-xs text-slate-500">{o.lifecycle_state} · {o.payment_state}</div>
            </div>
            <div className="font-semibold">{formatMoney(o.total_minor, o.currency)}</div>
          </Link>
        ))}
        {orders.length === 0 && <p className="text-slate-500">No orders yet.</p>}
      </div>
    </div>
  );
}
