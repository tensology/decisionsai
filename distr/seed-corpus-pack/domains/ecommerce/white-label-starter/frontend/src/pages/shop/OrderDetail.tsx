import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api, formatMoney } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function OrderDetail() {
  const { number } = useParams();
  const { user, loading } = useAuth();
  const [order, setOrder] = useState<any>(null);
  useEffect(() => {
    if (user && number) api.get(`/api/shop/orders/${number}/`).then(setOrder).catch(() => setOrder(null));
  }, [user, number]);
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (!order) return <div className="mx-auto max-w-3xl px-4 py-16 text-slate-500">Loading…</div>;

  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-bold">{order.number}</h1>
      <p className="text-sm text-slate-500 mt-1">
        payment: {order.payment_state} · fulfillment: {order.fulfillment_state} · lifecycle: {order.lifecycle_state}
      </p>
      <div className="mt-6 rounded-2xl border bg-white p-6 shadow-sm space-y-3">
        {order.items.map((it: any) => (
          <div key={it.id} className="flex justify-between text-sm">
            <span>{it.product_name} × {it.quantity}</span>
            <span>{formatMoney(it.line_total_minor, order.currency)}</span>
          </div>
        ))}
        <div className="border-t pt-3 flex justify-between font-semibold">
          <span>Total</span>
          <span>{formatMoney(order.total_minor, order.currency)}</span>
        </div>
        <div className="text-sm text-slate-600 pt-2">
          Ship to {order.shipping_name}, {order.shipping_line1}, {order.shipping_city} {order.shipping_postal_code}
        </div>
      </div>
    </div>
  );
}
