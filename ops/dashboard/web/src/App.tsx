import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { useHealth } from "@/hooks/useQueries";
import { useSession } from "@/stores/session";
import { AuthError } from "@/api/client";
import { api } from "@/api/client";

function AuthGate({ children }: { children: React.ReactNode }) {
  const authed = useSession((s) => s.authed);
  const setAuthed = useSession((s) => s.setAuthed);
  const qc = useQueryClient();

  // Probe a protected endpoint once on mount; if it 401s we flip to login.
  useEffect(() => {
    let cancelled = false;
    api
      .get("/api/overview")
      .then(() => !cancelled && setAuthed(true))
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof AuthError) setAuthed(false);
      });
    return () => {
      cancelled = true;
    };
  }, [setAuthed]);

  if (!authed) {
    return (
      <Login
        onAuthed={() => {
          setAuthed(true);
          qc.invalidateQueries();
        }}
      />
    );
  }
  return <>{children}</>;
}

export default function App() {
  // Health is always available (no auth) — harmless ping even at login.
  useHealth();
  return (
    <AuthGate>
      <Dashboard />
    </AuthGate>
  );
}
