import { AppShell } from "@/components/AppShell";
import { CalibrationPanel } from "@/components/CalibrationPanel";
import { Providers } from "@/components/Providers";

export default function CalibrationPage() {
  return (
    <Providers>
      <AppShell>
        <CalibrationPanel />
      </AppShell>
    </Providers>
  );
}
