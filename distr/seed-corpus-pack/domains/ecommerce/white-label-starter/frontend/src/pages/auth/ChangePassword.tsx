import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function ChangePassword() {
  const { user, loading } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/auth/password/change/", { current_password: current, new_password: next });
      setMsg("Password updated.");
    } catch (err: any) {
      setError(err.message);
    }
  }
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-6">Change password</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
        {msg && <p className="text-sm text-green-700">{msg}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <input type="password" required value={current} onChange={(e) => setCurrent(e.target.value)} placeholder="Current password" className="w-full rounded-lg border px-3 py-2" />
        <input type="password" required value={next} onChange={(e) => setNext(e.target.value)} placeholder="New password" className="w-full rounded-lg border px-3 py-2" />
        <button className="w-full rounded-lg bg-primary-600 text-white py-2.5">Update</button>
      </form>
    </div>
  );
}
