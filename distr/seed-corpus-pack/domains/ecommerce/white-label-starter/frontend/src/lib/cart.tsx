import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

type Line = {
  id: number;
  variant: number;
  sku: string;
  product_name: string;
  quantity: number;
  unit_price_minor: number;
  line_total_minor: number;
};

type Cart = {
  id: number;
  currency: string;
  lines: Line[];
  total_minor: number;
};

type CartCtx = {
  cart: Cart | null;
  refresh: () => Promise<void>;
  setQty: (variantId: number, quantity: number) => Promise<void>;
  count: number;
};

const Ctx = createContext<CartCtx>({ cart: null, refresh: async () => {}, setQty: async () => {}, count: 0 });

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await api.get<Cart>("/api/shop/cart/");
      setCart(c);
    } catch {
      setCart(null);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const setQty = async (variantId: number, quantity: number) => {
    const c = await api.post<Cart>("/api/shop/cart/", { variant_id: variantId, quantity });
    setCart(c);
  };

  const count = cart?.lines.reduce((n, l) => n + l.quantity, 0) ?? 0;

  return <Ctx.Provider value={{ cart, refresh, setQty, count }}>{children}</Ctx.Provider>;
}

export const useCart = () => useContext(Ctx);
