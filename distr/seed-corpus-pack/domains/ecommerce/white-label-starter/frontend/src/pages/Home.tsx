import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function Home() {
  const [meta, setMeta] = useState<{ site_name: string; tagline: string; hero_cta: string } | null>(null);
  useEffect(() => {
    api.get<typeof meta>("/api/pages/home/").then(setMeta).catch(() =>
      setMeta({ site_name: "White-Label Shop", tagline: "Quality goods, branded for you.", hero_cta: "Shop catalogue" })
    );
  }, []);

  return (
    <div>
      <section className="bg-gradient-to-br from-primary-700 via-primary-600 to-sky-500 text-white">
        <div className="mx-auto max-w-6xl px-4 py-24 md:py-32">
          <p className="text-primary-100 text-sm font-medium uppercase tracking-wider mb-3">White-label ready</p>
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight max-w-2xl">
            {meta?.site_name || "White-Label Shop"}
          </h1>
          <p className="mt-4 text-lg text-sky-50 max-w-xl">{meta?.tagline}</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link to="/shop" className="rounded-xl bg-white text-primary-700 px-5 py-3 font-semibold shadow hover:bg-primary-50">
              {meta?.hero_cta || "Shop catalogue"}
            </Link>
            <Link to="/about" className="rounded-xl border border-white/40 px-5 py-3 font-medium hover:bg-white/10">
              Learn more
            </Link>
          </div>
        </div>
      </section>
      <section className="mx-auto max-w-6xl px-4 py-16 grid md:grid-cols-3 gap-6">
        {[
          ["Tokenized brand", "Primary colours live in CSS variables and Unfold COLORS — swap once via BrandPack."],
          ["Commerce core", "Catalog, cart, idempotent checkout, orthogonal order axes, FakeGateway payments."],
          ["Admin included", "django-unfold skins every domain model with Order inlines ready for ops."],
        ].map(([t, d]) => (
          <div key={t} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="font-semibold text-slate-900">{t}</h3>
            <p className="mt-2 text-sm text-slate-600">{d}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
