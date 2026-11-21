import "server-only";

import { backendFetch } from "@/lib/api";

export type Memory = {
  id: string;
  kind: "contact" | "fact";
  text: string;
  name: string | null;
  email: string | null;
  updated_at: string;
};

export const listMemories = () => backendFetch<Memory[]>("/memories");
