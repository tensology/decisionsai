import { FormEvent, useState } from "react";
import { api } from "../../lib/api";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await api.post("/api/auth/password/forgot/", { email });
    setMsg("If that email exists, a reset link was sent (see console/locmem).");
  }
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-6">Forgot password</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
        {msg && <p className="text-sm text-green-700">{msg}</p>}
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="Email" className="w-full rounded-lg border px-3 py-2" />
        <button className="w-full rounded-lg bg-primary-600 text-white py-2.5">Send reset link</button>
      </form>
    </div>
  );
}
