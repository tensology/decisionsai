import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Props = { slug: string; fallbackTitle?: string; fallbackHtml?: string };

export default function Legal({ slug, fallbackTitle, fallbackHtml }: Props) {
  const [page, setPage] = useState<{ title: string; body_html: string } | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<{ title: string; body_html: string }>(`/api/pages/legal/${slug}/`)
      .then(setPage)
      .catch(() => {
        if (fallbackTitle) setPage({ title: fallbackTitle, body_html: fallbackHtml || "" });
        else setErr("Page not found. Run seed_demo.");
      });
  }, [slug, fallbackTitle, fallbackHtml]);

  if (err) return <div className="mx-auto max-w-3xl px-4 py-16 text-red-600">{err}</div>;
  if (!page) return <div className="mx-auto max-w-3xl px-4 py-16 text-slate-500">Loading…</div>;

  return (
    <article className="mx-auto max-w-3xl px-4 py-12 prose prose-slate">
      <h1 className="text-3xl font-bold text-slate-900 mb-6">{page.title}</h1>
      <div className="text-slate-700 leading-relaxed space-y-3" dangerouslySetInnerHTML={{ __html: page.body_html }} />
    </article>
  );
}
