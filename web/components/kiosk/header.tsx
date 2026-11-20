"use client";

import { useSyncExternalStore } from "react";
import { RiLogoutBoxLine, RiMoonLine, RiQuestionLine, RiSunLine } from "@remixicon/react";
import { Radio, RadioGroup } from "react-aria-components";
import { Logo } from "@/components/agent/logo";
import { Button } from "@/components/base/buttons/button";
import { useI18n, type Lang } from "@/lib/i18n";

function subscribeTheme(listener: () => void) {
  const observer = new MutationObserver(listener);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const getTheme = () => document.documentElement.classList.contains("dark");
const defaultTheme = () => false;

export function Header({ onHelp, onEnd, inSession }: { onHelp: () => void; onEnd: () => void; inSession: boolean }) {
  const { t, lang, setLang } = useI18n();
  const dark = useSyncExternalStore(subscribeTheme, getTheme, defaultTheme);

  return (
    <header className="workspace-header">
      <div className="workspace-width workspace-header-inner">
        <div className="workspace-identity">
          <Logo size={44} />
          <span className="workspace-airport text-headline-medium">
            {t("workspace.airport")} <bdi className="text-body-medium text-text-secondary">DXB</bdi>
          </span>
        </div>
        <div className="workspace-header-actions">
          <RadioGroup orientation="horizontal" aria-label={lang === "ar" ? "اللغة" : "Language"} value={lang} onChange={(value) => setLang(value as Lang)} className="workspace-language">
            <Radio value="en" className="workspace-language-option text-body-semibold"><span lang="en">English</span></Radio>
            <Radio value="ar" className="workspace-language-option text-body-semibold"><span lang="ar">عربي</span></Radio>
          </RadioGroup>
          <Button variant="ghost" iconOnly leadingIcon={dark ? RiSunLine : RiMoonLine} className="workspace-theme size-14"
            aria-label={t(dark ? "workspace.light" : "workspace.dark")}
            onClick={() => document.documentElement.classList.toggle("dark", !dark)} />
          <Button variant="secondary" leadingIcon={RiQuestionLine} className="workspace-help h-14 px-3"
            aria-label={t("ui.help")} onClick={onHelp}><span>{t("ui.help")}</span></Button>
          {inSession && (
            <Button variant="secondary" leadingIcon={RiLogoutBoxLine} className="workspace-end h-14 px-3"
              aria-label={t("ui.end_session")} onClick={onEnd}><span>{t("ui.end_session")}</span></Button>
          )}
        </div>
      </div>
    </header>
  );
}
