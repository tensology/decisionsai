import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function ChangeEmail() {
  const { user, loading } = useAuth();
  const [newEmail, setNewEmail] = useState("");
  const [msg, setMsg] = useState("");
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await api.post("/api/auth/email/change/", { new_email: newEmail });
    setMsg("Confirmation sent to the new address (token stub).");
  }
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-6">Change email</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
        {msg && <p className="text-sm text-green-700">{msg}</p>}
        <input type="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)}
          placeholder="New email" className="w-full rounded-lg border px-3 py-2" />
        <button className="w-full rounded-lg bg-primary-600 text-white py-2.5">Request change</button>
      </form>
    </div>
  );
}
