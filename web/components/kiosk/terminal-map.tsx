"use client";

import { useState } from "react";
import { RiExternalLinkLine, RiMap2Line, RiTrainLine, RiWalkLine } from "@remixicon/react";
import { ButtonLink } from "@/components/base/buttons/button";
import { Modal, ModalBody, ModalDialog, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { useI18n } from "@/lib/i18n";

const TERMINALS = ["3", "1", "2"] as const;
type Terminal = typeof TERMINALS[number];
const OFFICIAL_MAP = "https://dubaiairports.ae/express-maps?areafilter=connectingflights";
const OFFICIAL_TRANSFER = "https://dubaiairports.ae/information/transfers";

export function TerminalMap({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [terminal, setTerminal] = useState<Terminal>("3");
  const terminalLabel = (value: Terminal) => t("maps.terminal", { terminal: value });
  const gate = (code: string) => <span className="terminal-station text-headline-semibold">{t("maps.gates", { concourse: code })}</span>;
  const train = <span className="terminal-link text-body-medium"><RiTrainLine aria-hidden className="size-5" />{t("maps.train")}</span>;
  const walk = <span className="terminal-link text-body-medium"><RiWalkLine aria-hidden className="size-5" />{t("maps.walk")}</span>;
  return (
    <Modal isOpen onOpenChange={(value) => !value && onClose()} size="lg">
      <ModalDialog>
        <ModalHeader title={t("maps.overview")} description={t("maps.schematic")} icon={RiMap2Line} />
        <ModalBody className="flex flex-col gap-4">
          <SegmentedControl aria-label={t("maps.title")} selectedKeys={[terminal]}
            onSelectionChange={(keys) => { const next = [...keys][0]; if (TERMINALS.includes(next as Terminal)) setTerminal(next as Terminal); }}>
            {TERMINALS.map((item) => <SegmentedControlItem key={item} id={item}>{terminalLabel(item)}</SegmentedControlItem>)}
          </SegmentedControl>
          <div className="terminal-diagram" aria-hidden="true">
            {terminal === "3" ? <>{gate("A")}{train}{gate("B")}{walk}{gate("C")}</>
              : terminal === "1" ? <><span className="terminal-station text-headline-semibold">{terminalLabel("1")}</span>{train}{gate("D")}</>
              : gate("F")}
          </div>
          <p className="text-headline-medium" role="status">{t("maps.terminal_" + terminal)}</p>
          <p className="text-headline-regular text-text-secondary">{t("maps.transfer")}</p>
        </ModalBody>
        <ModalFooter className="flex-wrap">
          <ButtonLink variant="ghost" trailingIcon={RiExternalLinkLine} className="h-14 px-2" href={OFFICIAL_TRANSFER} target="_blank" rel="noreferrer noopener">
            {t("maps.source")}
          </ButtonLink>
          <ButtonLink variant="primary" trailingIcon={RiExternalLinkLine} className="workspace-primary h-14 px-4" href={OFFICIAL_MAP} target="_blank" rel="noreferrer noopener">
            {t("maps.official")}
          </ButtonLink>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}
