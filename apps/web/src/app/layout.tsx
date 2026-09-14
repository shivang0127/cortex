import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Second Brain",
  description: "An AI system that builds and maintains a structured model of what you know.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <header className="border-b border-border bg-panel">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between px-4 py-3">
            <div className="flex items-baseline gap-3">
              <span className="text-lg font-semibold tracking-tight">Second Brain</span>
              <span className="text-sm text-muted">what you know, structured</span>
            </div>
            <span className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted">
              Phase 0 · foundations
            </span>
          </div>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
