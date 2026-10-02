import Legal from "./Legal";
export default function Contact() {
  return (
    <div>
      <Legal slug="contact" fallbackTitle="Contact" fallbackHtml="<p>Email hello@example.com</p>" />
      <div className="mx-auto max-w-3xl px-4 pb-16">
        <form className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 space-y-4 shadow-sm" onSubmit={(e) => e.preventDefault()}>
          <h2 className="font-semibold text-lg">Send a message</h2>
          <input className="w-full rounded-lg border border-slate-300 px-3 py-2" placeholder="Your name" />
          <input className="w-full rounded-lg border border-slate-300 px-3 py-2" type="email" placeholder="Email" />
          <textarea className="w-full rounded-lg border border-slate-300 px-3 py-2" rows={4} placeholder="How can we help?" />
          <button className="rounded-lg bg-primary-600 text-white px-4 py-2 font-medium hover:bg-primary-700" type="submit">
            Send (demo — not wired)
          </button>
        </form>
      </div>
    </div>
  );
}
