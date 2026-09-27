import { AppShell } from "@/components/AppShell";
import { CatalystsPanel } from "@/components/CatalystsPanel";
import { Providers } from "@/components/Providers";

export default function CatalystsPage() {
  return (
    <Providers>
      <AppShell>
        <CatalystsPanel />
      </AppShell>
    </Providers>
  );
}
