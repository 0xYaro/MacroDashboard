import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";

import "./globals.css";

/**
 * Two faces, one job each, both self-hosted by Next's font pipeline.
 *
 * IBM Plex Sans carries every label, heading and sentence. IBM Plex Mono carries
 * figures and nothing else, with tabular numerals so a column cannot change width
 * on a tick. Splitting them is the whole of the typographic hierarchy: a label set
 * in mono competes with the figure beside it for the reader's attention, and on a
 * page whose subject is the figures the label must lose.
 *
 * Both go through `next/font` rather than a Google Fonts stylesheet so they are
 * served from this origin: a numeric column that falls back to a proportional face
 * while a third-party stylesheet loads is a column that changes width on first
 * paint, and that is the exact jitter the whole type system exists to prevent.
 *
 * Sans arrived later than mono. Accounting added it, `--sans` there became
 * `var(--font-plex-sans)`, and a `var()` with no fallback that names an undeclared
 * property is invalid at computed-value time: copying the token without the loader
 * would not have degraded to the next font in the stack, it would have dropped the
 * declaration entirely.
 */
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Macro · the regime read",
  description:
    "A daily macro terminal for one operator: the snapshot, the phase calls, the scenario set and the week's setups, re-scored twice a day.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0b0f14",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
