import { useEffect, useState } from "react";
import { Activity, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/api/client";
import { liveBus, type ConnectionState } from "@/api/ws";
import { useSession } from "@/stores/session";

function stateToVariant(s: ConnectionState) {
  if (s === "open") return "ok" as const;
  if (s === "connecting") return "warn" as const;
  return "err" as const;
}

export function Header() {
  const setAuthed = useSession((s) => s.setAuthed);
  const [ws, setWs] = useState<ConnectionState>("idle");

  useEffect(() => liveBus.onStatus(setWs), []);

  async function logout() {
    await api.post("/api/auth/logout");
    setAuthed(false);
  }

  return (
    <header className="flex items-center justify-between border-b px-6 py-3">
      <div className="flex items-center gap-3">
        <Activity className="h-4 w-4 text-primary" />
        <div className="text-sm font-medium tracking-wide">Miner Control Room</div>
        <Badge variant="muted">M1 · dashboard</Badge>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          ws <Badge variant={stateToVariant(ws)}>{ws}</Badge>
        </div>
        <Button size="sm" variant="ghost" onClick={logout} title="log out">
          <LogOut className="h-3.5 w-3.5" />
        </Button>
      </div>
    </header>
  );
}
