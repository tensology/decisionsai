import { FormEvent, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function Profile() {
  const { user, loading, refresh } = useAuth();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (user) {
      setName(user.name || "");
      setPhone(user.phone || "");
    }
  }, [user]);

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await api.patch("/api/auth/profile/", { name, phone });
    await refresh();
    setMsg("Profile saved.");
  }

  return (
    <div className="mx-auto max-w-lg px-4 py-12">
      <h1 className="text-2xl font-bold mb-6">Your profile</h1>
      <form onSubmit={onSubmit} className="rounded-2xl border bg-white p-6 space-y-4 shadow-sm">
        {msg && <p className="text-sm text-green-700">{msg}</p>}
        <p className="text-sm text-slate-500">{user.email} {user.email_verified ? "· verified" : "· unverified"}</p>
        <label className="block text-sm font-medium">Name
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" />
        </label>
        <label className="block text-sm font-medium">Phone
          <input value={phone} onChange={(e) => setPhone(e.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" />
        </label>
        <button className="rounded-lg bg-primary-600 text-white px-4 py-2">Save</button>
        <div className="flex gap-4 text-sm pt-2">
          <Link to="/change-email" className="text-primary-700">Change email</Link>
          <Link to="/change-password" className="text-primary-700">Change password</Link>
        </div>
      </form>
    </div>
  );
}
