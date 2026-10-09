import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Pouse · Virtual AI Office", description: "PC sourcing with a virtual team of four agents" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
