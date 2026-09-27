import { AppShell } from "@/components/AppShell";
import { MacroPanel } from "@/components/MacroPanel";
import { Providers } from "@/components/Providers";

/**
 * The Desk is the root, because it is the page a trader opens first: the
 * call, and what has moved against it since it was made.
 */
export default function DeskPage() {
  return (
    <Providers>
      <AppShell>
        <MacroPanel page="desk" />
      </AppShell>
    </Providers>
  );
}
