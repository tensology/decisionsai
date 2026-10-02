import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useCart } from "../lib/cart";

const nav = "px-3 py-2 rounded-lg text-sm font-medium text-slate-600 hover:text-primary-700 hover:bg-primary-50";
const active = "px-3 py-2 rounded-lg text-sm font-medium text-primary-700 bg-primary-50";

export default function Layout() {
  const { user, logout } = useAuth();
  const { count } = useCart();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto max-w-6xl px-4 h-16 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2 font-semibold text-lg text-slate-900">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-primary-600 text-white text-sm shadow-sm">
              WL
            </span>
            <span>White-Label Shop</span>
          </Link>
          <nav className="hidden md:flex items-center gap-1">
            <NavLink to="/shop" className={({ isActive }) => (isActive ? active : nav)}>Catalogue</NavLink>
            <NavLink to="/about" className={({ isActive }) => (isActive ? active : nav)}>About</NavLink>
            <NavLink to="/contact" className={({ isActive }) => (isActive ? active : nav)}>Contact</NavLink>
          </nav>
          <div className="flex items-center gap-2">
            <Link to="/cart" className="relative rounded-lg border border-slate-200 px-3 py-2 text-sm hover:border-primary-300">
              Cart
              {count > 0 && (
                <span className="absolute -top-2 -right-2 h-5 min-w-5 rounded-full bg-primary-600 text-white text-xs flex items-center justify-center px-1">
                  {count}
                </span>
              )}
            </Link>
            {user ? (
              <>
                <Link to="/orders" className={nav}>Orders</Link>
                <Link to="/profile" className={nav}>{user.name || user.email}</Link>
                <button onClick={() => logout()} className="text-sm text-slate-500 hover:text-slate-800 px-2">Logout</button>
              </>
            ) : (
              <Link to="/login" className="rounded-lg bg-primary-600 text-white px-3 py-2 text-sm font-medium hover:bg-primary-700">
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 bg-white mt-12">
        <div className="mx-auto max-w-6xl px-4 py-10 grid sm:grid-cols-3 gap-8 text-sm text-slate-600">
          <div>
            <div className="font-semibold text-slate-900 mb-2">White-Label Shop</div>
            <p>Tokenized brand starter — swap primary CSS vars when your BrandPack arrives.</p>
          </div>
          <div className="space-y-1">
            <Link className="block hover:text-primary-700" to="/terms">Terms</Link>
            <Link className="block hover:text-primary-700" to="/privacy">Privacy</Link>
            <Link className="block hover:text-primary-700" to="/about">About</Link>
          </div>
          <div className="text-slate-500">© {new Date().getFullYear()} Demo storefront. Local SQLite only.</div>
        </div>
      </footer>
    </div>
  );
}
