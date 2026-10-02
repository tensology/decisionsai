import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import About from "./pages/About";
import Contact from "./pages/Contact";
import Legal from "./pages/Legal";
import Login from "./pages/auth/Login";
import Register from "./pages/auth/Register";
import ForgotPassword from "./pages/auth/ForgotPassword";
import ResetPassword from "./pages/auth/ResetPassword";
import Profile from "./pages/auth/Profile";
import ChangeEmail from "./pages/auth/ChangeEmail";
import ChangePassword from "./pages/auth/ChangePassword";
import Catalogue from "./pages/shop/Catalogue";
import ProductDetail from "./pages/shop/ProductDetail";
import CartPage from "./pages/shop/CartPage";
import Checkout from "./pages/shop/Checkout";
import Orders from "./pages/shop/Orders";
import OrderDetail from "./pages/shop/OrderDetail";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="about" element={<About />} />
        <Route path="contact" element={<Contact />} />
        <Route path="terms" element={<Legal slug="terms" />} />
        <Route path="privacy" element={<Legal slug="privacy" />} />
        <Route path="login" element={<Login />} />
        <Route path="register" element={<Register />} />
        <Route path="forgot-password" element={<ForgotPassword />} />
        <Route path="reset-password" element={<ResetPassword />} />
        <Route path="profile" element={<Profile />} />
        <Route path="change-email" element={<ChangeEmail />} />
        <Route path="change-password" element={<ChangePassword />} />
        <Route path="shop" element={<Catalogue />} />
        <Route path="shop/:slug" element={<ProductDetail />} />
        <Route path="cart" element={<CartPage />} />
        <Route path="checkout/*" element={<Checkout />} />
        <Route path="orders" element={<Orders />} />
        <Route path="orders/:number" element={<OrderDetail />} />
      </Route>
    </Routes>
  );
}
