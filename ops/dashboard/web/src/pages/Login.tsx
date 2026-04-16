import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/api/client";

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api.post("/api/auth/login", { password });
      onAuthed();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setErr("wrong password");
      else setErr(e instanceof Error ? e.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Miner Control Room</CardTitle>
          <div className="text-sm text-foreground">sign in</div>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Input
              type="password"
              placeholder="dashboard password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
            {err && <div className="text-xs text-err">{err}</div>}
            <Button type="submit" disabled={busy || password.length === 0}>
              {busy ? "signing in…" : "sign in"}
            </Button>
            <div className="text-[11px] text-muted-foreground">
              Set <span className="font-mono">OPS_DASHBOARD_PASSWORD</span> in the service env.
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
