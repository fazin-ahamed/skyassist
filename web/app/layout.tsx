import type { Metadata, Viewport } from "next";
import { Barlow, Barlow_Condensed, Noto_Sans_Arabic } from "next/font/google";
import "@/styles/globals.css";
import "./app.css";
import { Providers } from "@/components/providers";

const barlow = Barlow({ variable: "--font-barlow", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const condensed = Barlow_Condensed({ variable: "--font-barlow-condensed", subsets: ["latin"], weight: ["600", "700"] });
const arabic = Noto_Sans_Arabic({ variable: "--font-arabic", subsets: ["arabic"] });

export const metadata: Metadata = {
  title: "SkyAssist",
  description: "Terminal connection guide",
};

const THEME_SCRIPT = `try{var q=new URLSearchParams(location.search).get("theme");document.documentElement.classList.toggle("dark",q==="dark")}catch(e){}`;

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${barlow.variable} ${condensed.variable} ${arabic.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full bg-transparent text-text-primary">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
