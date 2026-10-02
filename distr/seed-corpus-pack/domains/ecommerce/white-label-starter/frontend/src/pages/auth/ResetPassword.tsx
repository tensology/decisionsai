import { FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const [token, setToken] = useState(params.get("token") || "");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const nav = useNavigate();
  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await api.post("/api/auth/password/reset/", { token, new_password: password });
    setMsg("Password updated.");
    setTimeout(() => nav("/login"), 1000);
  }
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-6">Reset password</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
        {msg && <p className="text-sm text-green-700">{msg}</p>}
        <input required value={token} onChange={(e) => setToken(e.target.value)} placeholder="Reset token" className="w-full rounded-lg border px-3 py-2" />
        <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="New password" className="w-full rounded-lg border px-3 py-2" />
        <button className="w-full rounded-lg bg-primary-600 text-white py-2.5">Update password</button>
      </form>
    </div>
  );
}
