import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, formatMoney } from "../../lib/api";
import { useCart } from "../../lib/cart";

type Variant = { id: number; sku: string; label: string; unit_price_minor: number; currency: string; stock_on_hand: number };
type Product = { id: number; name: string; slug: string; description: string; variants: Variant[] };

export default function ProductDetail() {
  const { slug } = useParams();
  const [product, setProduct] = useState<Product | null>(null);
  const [variantId, setVariantId] = useState<number | null>(null);
  const { setQty } = useCart();
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.get<Product>(`/api/shop/catalog/${slug}/`).then((p) => {
      setProduct(p);
      setVariantId(p.variants[0]?.id ?? null);
    });
  }, [slug]);

  if (!product) return <div className="mx-auto max-w-6xl px-4 py-16 text-slate-500">Loading…</div>;
  const v = product.variants.find((x) => x.id === variantId);

  return (
    <div className="mx-auto max-w-6xl px-4 py-12 grid md:grid-cols-2 gap-10">
      <div className="rounded-2xl bg-gradient-to-br from-primary-100 to-sky-50 h-80 flex items-center justify-center text-6xl text-primary-600 font-bold">
        {product.name.slice(0, 1)}
      </div>
      <div>
        <h1 className="text-3xl font-bold">{product.name}</h1>
        <p className="mt-3 text-slate-600">{product.description}</p>
        <div className="mt-6 text-2xl font-semibold text-primary-700">
          {v ? formatMoney(v.unit_price_minor, v.currency) : "—"}
        </div>
        {product.variants.length > 1 && (
          <select className="mt-4 rounded-lg border px-3 py-2" value={variantId ?? ""} onChange={(e) => setVariantId(Number(e.target.value))}>
            {product.variants.map((x) => (
              <option key={x.id} value={x.id}>{x.label || x.sku}</option>
            ))}
          </select>
        )}
        <button
          className="mt-6 rounded-xl bg-primary-600 text-white px-6 py-3 font-medium hover:bg-primary-700 disabled:opacity-50"
          disabled={!v}
          onClick={async () => {
            if (!v) return;
            await setQty(v.id, 1);
            setMsg("Added to cart");
          }}
        >
          Add to cart
        </button>
        {msg && <p className="mt-3 text-sm text-green-700">{msg}</p>}
        {v && <p className="mt-2 text-xs text-slate-400">{v.stock_on_hand} in stock · SKU {v.sku}</p>}
      </div>
    </div>
  );
}
