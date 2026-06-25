import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
    title: "Codex Proxy · Console",
    description: "Admin console for the Codex team proxy.",
};

// Keep mobile browsers at the intended CSS scale. Without an explicit
// viewport contract, embedded browsers may autoscale the console on first load.
export const viewport: Viewport = {
    width: "device-width",
    initialScale: 1,
    viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
    return (
        <html lang="en">
            <body className="min-h-screen font-sans antialiased">{children}</body>
        </html>
    );
}
