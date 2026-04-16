import { create } from "zustand";

interface SessionState {
  authed: boolean;
  setAuthed: (v: boolean) => void;
}

export const useSession = create<SessionState>((set) => ({
  authed: false,
  setAuthed: (authed) => set({ authed }),
}));
