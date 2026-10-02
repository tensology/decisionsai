import { FormEvent, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, formatMoney } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useCart } from "../../lib/cart";

type Step = "address" | "payment" | "confirm" | "success" | "fail";

export default function Checkout() {
  const { user, loading } = useAuth();
  const { cart, refresh } = useCart();
  const [step, setStep] = useState<Step>("address");
  const [addr, setAddr] = useState({ name: "", line1: "", line2: "", city: "", region: "", postal_code: "", country: "ZA" });
  const [order, setOrder] = useState<any>(null);
  const [error, setError] = useState("");
  const nav = useNavigate();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;

  async function place(e?: FormEvent) {
    e?.preventDefault();
    setError("");
    try {
      const key = `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const data = await api.post<any>("/api/shop/checkout/", { ...addr, idempotency_key: key });
      setOrder(data);
      setStep("payment");
      await refresh();
    } catch (err: any) {
      setError(err.message);
      setStep("fail");
    }
  }

  async function pay(succeed: boolean) {
    if (!order?.payment_intent_id) return;
    // Fake gateway: fetch intent then webhook
    const intent = await api.get<any>(`/api/payments/intents/${order.payment_intent_id}/`);
    const wr = await fetch("/api/payments/webhook/", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: succeed ? "payment.succeeded" : "payment.failed",
        gateway_ref: intent.gateway_ref,
      }),
    });
    if (!wr.ok) {
      setStep("fail");
      return;
    }
    setStep(succeed ? "success" : "fail");
    if (succeed) setTimeout(() => nav(`/orders/${order.number}`), 1200);
  }

  return (
    <div className="mx-auto max-w-xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-2">Checkout</h1>
      <p className="text-slate-500 text-sm mb-8">Steps: address → payment → confirm</p>
      <div className="flex gap-2 mb-8 text-xs font-medium">
        {(["address", "payment", "confirm"] as Step[]).map((s) => (
          <span key={s} className={`px-3 py-1 rounded-full ${step === s || (step === "success" && s === "confirm") ? "bg-primary-600 text-white" : "bg-slate-200 text-slate-600"}`}>{s}</span>
        ))}
      </div>

      {step === "address" && (
        <form onSubmit={(e) => { e.preventDefault(); setStep("confirm"); }} className="rounded-2xl border bg-white p-6 space-y-3 shadow-sm">
          {(["name", "line1", "line2", "city", "region", "postal_code"] as const).map((f) => (
            <input key={f} required={f !== "line2" && f !== "region"}
              placeholder={f.replace("_", " ")}
              value={(addr as any)[f]}
              onChange={(e) => setAddr({ ...addr, [f]: e.target.value })}
              className="w-full rounded-lg border px-3 py-2 capitalize" />
          ))}
          <button className="w-full rounded-lg bg-primary-600 text-white py-2.5">Continue to review</button>
        </form>
      )}

      {step === "confirm" && (
        <div className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
          <p className="text-sm text-slate-600">Ship to {addr.line1}, {addr.city}. Cart total {cart ? formatMoney(cart.total_minor, cart.currency) : "—"}.</p>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button onClick={() => place()} className="w-full rounded-lg bg-primary-600 text-white py-2.5">Place order</button>
          <button onClick={() => setStep("address")} className="w-full text-sm text-slate-500">Back</button>
        </div>
      )}

      {step === "payment" && order && (
        <div className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
          <p>Order <strong>{order.number}</strong> — {formatMoney(order.total_minor, order.currency)}</p>
          <p className="text-sm text-slate-500">FakeGateway demo — no real charges.</p>
          <div className="flex gap-3">
            <button onClick={() => pay(true)} className="flex-1 rounded-lg bg-primary-600 text-white py-2.5">Pay successfully</button>
            <button onClick={() => pay(false)} className="flex-1 rounded-lg border py-2.5">Simulate failure</button>
          </div>
        </div>
      )}

      {step === "success" && (
        <div className="rounded-2xl border border-green-200 bg-green-50 p-6 text-green-800">
          Payment succeeded. Redirecting to order…
        </div>
      )}
      {step === "fail" && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-800 space-y-3">
          <p>Payment failed or checkout error. {error}</p>
          <Link to="/cart" className="text-primary-700 underline">Return to cart</Link>
        </div>
      )}
    </div>
  );
}
