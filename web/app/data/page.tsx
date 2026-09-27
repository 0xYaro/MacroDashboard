import { AppShell } from "@/components/AppShell";
import { MacroPanel } from "@/components/MacroPanel";
import { Providers } from "@/components/Providers";

export default function DataPage() {
  return (
    <Providers>
      <AppShell>
        <MacroPanel page="data" />
      </AppShell>
    </Providers>
  );
}
