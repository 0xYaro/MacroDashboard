import { AppShell } from "@/components/AppShell";
import { HistoryPanel } from "@/components/HistoryPanel";
import { Providers } from "@/components/Providers";

export default function HistoryPage() {
  return (
    <Providers>
      <AppShell>
        <HistoryPanel />
      </AppShell>
    </Providers>
  );
}
