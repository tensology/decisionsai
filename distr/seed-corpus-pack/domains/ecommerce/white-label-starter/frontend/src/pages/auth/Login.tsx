import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const { refresh } = useAuth();
  const nav = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/auth/login/", { email, password });
      await refresh();
      nav("/shop");
    } catch (err: any) {
      setError(err.message || "Login failed");
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-6">Sign in</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border border-slate-200 bg-white p-6 space-y-4 shadow-sm">
        {error && <p className="text-sm text-red-600">{error}</p>}
        <label className="block text-sm font-medium">Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <label className="block text-sm font-medium">Password
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <button className="w-full rounded-lg bg-primary-600 text-white py-2.5 font-medium hover:bg-primary-700">Sign in</button>
        <div className="text-sm text-slate-600 flex justify-between">
          <Link to="/register" className="text-primary-700">Create account</Link>
          <Link to="/forgot-password" className="text-primary-700">Forgot password?</Link>
        </div>
      </form>
    </div>
  );
}
