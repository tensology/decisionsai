import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { DashboardShell } from "@/components/DashboardShell";
import { getSession } from "@/lib/auth-server";
import { DEMO_USER, SKIP_AUTH } from "@/lib/demo-mode";

export const Route = createFileRoute("/_app")({
  beforeLoad: async () => {
    // Demo-deploy mode: run as a fixed user, no login (see `@/lib/demo-mode`).
    if (SKIP_AUTH) {
      return { user: DEMO_USER };
    }
    const session = await getSession();
    if (!session) {
      throw redirect({ to: "/login" });
    }
    return { user: session.user };
  },
  component: AppLayout,
});

function AppLayout() {
  return (
    <DashboardShell>
      <Outlet />
    </DashboardShell>
  );
}
