import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatMoney } from "../../lib/api";

type Product = {
  id: number; name: string; slug: string; description: string;
  from_price_minor: number | null; currency: string; category_name?: string;
};

export default function Catalogue() {
  const [items, setItems] = useState<Product[]>([]);
  useEffect(() => {
    api.get<Product[]>("/api/shop/catalog/").then(setItems);
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-4 py-12">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold">Catalogue</h1>
          <p className="text-slate-600 mt-1">Curated essentials for your white-label demo.</p>
        </div>
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {items.map((p) => (
          <Link key={p.id} to={`/shop/${p.slug}`}
            className="group rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm hover:shadow-md hover:border-primary-200 transition">
            <div className="h-40 bg-gradient-to-br from-primary-100 to-sky-50 flex items-center justify-center text-primary-600 font-semibold text-lg">
              {p.name.slice(0, 1)}
            </div>
            <div className="p-5">
              <div className="text-xs uppercase tracking-wide text-slate-400">{p.category_name || "General"}</div>
              <h2 className="font-semibold text-lg mt-1 group-hover:text-primary-700">{p.name}</h2>
              <p className="text-sm text-slate-600 line-clamp-2 mt-1">{p.description}</p>
              <div className="mt-3 font-semibold text-primary-700">
                {p.from_price_minor != null ? formatMoney(p.from_price_minor, p.currency) : "—"}
              </div>
            </div>
          </Link>
        ))}
        {items.length === 0 && <p className="text-slate-500">No products yet — run <code>seed_demo</code>.</p>}
      </div>
    </div>
  );
}
