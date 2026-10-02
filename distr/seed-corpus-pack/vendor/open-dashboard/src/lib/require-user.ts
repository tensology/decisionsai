import { getRequest } from "@tanstack/react-start/server";
import { DEMO_USER, SKIP_AUTH } from "@/lib/demo-mode";

/**
 * Assert there is an authenticated user. Call ONLY inside server-fn handlers
 * that read or mutate protected data — throws if there is no session.
 *
 * Kept in its own module (separate from any client-imported file) so the
 * server-only auth/`getRequest` chain never leaks into the browser bundle.
 *
 * `@/lib/auth-provider` (and therefore `@/lib/auth`/`@/db`/`pg`) is imported
 * dynamically so demo-deploy mode never pulls the database client into the
 * statically-loaded server graph (keeps the Cloudflare/Workers bundle clean).
 */
export async function requireUser() {
  if (SKIP_AUTH) {
    return DEMO_USER;
  }
  const { authProvider } = await import("@/lib/auth-provider");
  const { headers } = getRequest();
  const session = await authProvider.getSession(headers);
  if (!session) {
    throw new Error("UNAUTHORIZED");
  }
  return session.user;
}
