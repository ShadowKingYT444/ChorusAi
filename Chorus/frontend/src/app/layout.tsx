import type { Metadata } from "next";
import { Inter, Space_Grotesk, Geist_Mono } from "next/font/google";
import { PageTransition } from "@/components/ui/page-transition";
import { SetupGate } from "@/components/setup-gate";
import { SolanaProviders } from "@/lib/solana/wallet-provider";
import "./globals.css";

const inter = Inter({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Chorus",
  description: "Chorus is a private swarm review workspace for RFCs, launches, architecture proposals, and risk decisions.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} ${spaceGrotesk.variable} antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-screen relative overflow-hidden bg-black text-foreground">
        <SolanaProviders>
          <SetupGate>
            <PageTransition>{children}</PageTransition>
          </SetupGate>
        </SolanaProviders>
      </body>
    </html>
  );
}
