import { Link } from "react-router-dom";
import { formatMoney } from "../../lib/api";
import { useCart } from "../../lib/cart";

export default function CartPage() {
  const { cart, setQty } = useCart();
  if (!cart) return <div className="mx-auto max-w-3xl px-4 py-16">Loading cart…</div>;

  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-6">Your cart</h1>
      {cart.lines.length === 0 ? (
        <p className="text-slate-600">Cart is empty. <Link to="/shop" className="text-primary-700">Browse catalogue</Link></p>
      ) : (
        <div className="space-y-4">
          {cart.lines.map((l) => (
            <div key={l.id} className="flex items-center justify-between rounded-xl border bg-white p-4 shadow-sm">
              <div>
                <div className="font-medium">{l.product_name}</div>
                <div className="text-sm text-slate-500">{l.sku} · {formatMoney(l.unit_price_minor, cart.currency)}</div>
              </div>
              <div className="flex items-center gap-3">
                <input type="number" min={0} value={l.quantity}
                  onChange={(e) => setQty(l.variant, Number(e.target.value))}
                  className="w-16 rounded-lg border px-2 py-1 text-center" />
                <div className="w-24 text-right font-medium">{formatMoney(l.line_total_minor, cart.currency)}</div>
              </div>
            </div>
          ))}
          <div className="flex items-center justify-between pt-4 border-t">
            <div className="text-lg font-semibold">Total {formatMoney(cart.total_minor, cart.currency)}</div>
            <Link to="/checkout" className="rounded-xl bg-primary-600 text-white px-5 py-3 font-medium hover:bg-primary-700">
              Checkout
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
