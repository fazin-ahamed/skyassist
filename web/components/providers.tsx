"use client";

import { DirectionProvider } from "@/components/foundations/direction/direction";
import { Toaster } from "@/components/base/toast/toast";
import { I18nStore } from "@/lib/i18n";
import { useEffect } from "react";

/** Keeps a copy of the app so the page still opens if the service is restarting or the browser reopens offline. */
function RegisterWorker() {
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <I18nStore>
      {({ lang }) => (
        <DirectionProvider locale={lang}>
          {children}
          <Toaster />
          <RegisterWorker />
        </DirectionProvider>
      )}
    </I18nStore>
  );
}
