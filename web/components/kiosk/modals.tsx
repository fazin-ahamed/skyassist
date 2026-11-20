"use client";

import { useState } from "react";
import { RiAlarmWarningLine, RiCustomerService2Line, RiQuestionLine, RiShieldCheckLine, RiTimeLine, RiWheelchairLine } from "@remixicon/react";
import { RadioGroup } from "react-aria-components";
import { Button } from "@/components/base/buttons/button";
import { CheckboxCard } from "@/components/base/checkbox/checkbox-card";
import { Modal, ModalBody, ModalDialog, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { ProgressTrack } from "@/components/base/progress/progress";
import { RadioCard } from "@/components/base/radio/radio-card";
import { Notice } from "@/components/kiosk/ui";
import { useI18n } from "@/lib/i18n";

export function HelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <Modal isOpen={open} onOpenChange={(o) => !o && onClose()} size="md">
      <ModalDialog className="paper">
        <ModalHeader title={t("ui.help")} description={t("kiosk.help.body")} icon={RiQuestionLine} />
        <ModalBody>
          <Notice tone="orange" icon={RiAlarmWarningLine}>{t("ui.urgent.help")}</Notice>
        </ModalBody>
        <ModalFooter>
          <Button variant="primary" className="h-12 rounded-lg px-6" onClick={onClose}>{t("kiosk.help.close")}</Button>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}

export function PrivacyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <Modal isOpen={open} onOpenChange={(value) => !value && onClose()} size="md">
      <ModalDialog>
        <ModalHeader title={t("workspace.privacy")} icon={RiShieldCheckLine} />
        <ModalBody className="flex flex-col gap-4 text-headline-regular text-text-secondary">
          <p>{t("workspace.privacy_body")}</p>
          <p>{t("workspace.privacy_reset")}</p>
          <p>{t("workspace.privacy_staff")}</p>
        </ModalBody>
        <ModalFooter>
          <Button variant="primary" className="workspace-primary h-14 px-6" onClick={onClose}>{t("agent.dismiss")}</Button>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}

export function MobilityModal({ open, onClose, onChoose }: { open: boolean; onClose: () => void; onChoose: () => void }) {
  const { t } = useI18n();
  return (
    <Modal isOpen={open} onOpenChange={(value) => !value && onClose()} size="md">
      <ModalDialog>
        <ModalHeader title={t("workspace.mobility")} icon={RiWheelchairLine} />
        <ModalBody className="flex flex-col gap-4 text-headline-regular text-text-secondary">
          <p>{t("workspace.mobility_hint")}</p>
          <p>{t("kiosk.help.body")}</p>
        </ModalBody>
        <ModalFooter>
          <Button variant="secondary" className="h-14 px-5" onClick={onClose}>{t("agent.dismiss")}</Button>
          <Button variant="primary" className="workspace-primary h-14 px-5" onClick={onChoose}>{t("workspace.use_step_free")}</Button>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}

export function IdleModal({ open, remaining, total, onKeep, onEnd }: { open: boolean; remaining: number; total: number; onKeep: () => void; onEnd: () => void }) {
  const { t } = useI18n();
  return (
    <Modal isOpen={open} size="sm" isDismissable={false} isKeyboardDismissDisabled>
      <ModalDialog role="alertdialog">
        <ModalHeader title={t("ui.idle_countdown", { seconds: remaining })} icon={RiTimeLine} />
        <ModalBody>
          <ProgressTrack percentage={Math.max(0, (remaining / total) * 100)} size="lg" />
        </ModalBody>
        <ModalFooter>
          <Button variant="secondary" className="h-14 rounded-lg px-4" onClick={onEnd}>{t("ui.end_session")}</Button>
          <Button autoFocus variant="primary" className="workspace-primary h-14 rounded-lg px-4" onClick={onKeep}>{t("ui.extend_session")}</Button>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}

interface AssistProps {
  open: boolean;
  pickupLabel: string;
  onClose: () => void;
  onSubmit: (service: string) => Promise<boolean>;
  error: string;
}

export function AssistModal({ open, pickupLabel, onClose, onSubmit, error }: AssistProps) {
  const { t } = useI18n();
  const [service, setService] = useState("wheelchair");
  const [stay, setStay] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Modal isOpen={open} onOpenChange={(o) => !o && onClose()} size="lg">
      <ModalDialog className="paper">
        <ModalHeader title={t("kiosk.assist.title")} description={`${t("ui.staff.pickup")}: ${pickupLabel}`} icon={RiWheelchairLine} />
        <ModalBody className="flex flex-col gap-5">
          <div className="rounded-xl bg-tag-wash p-4">
            <p className="mb-2 text-body-semibold">{t("kiosk.assist.how_title")}</p>
            <ol className="flex flex-col gap-2">
              {[1, 2, 3].map((n) => (
                <li key={n} className="flex gap-3 text-body-regular">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-600 text-body-2-semibold tabular-nums text-text-white">{n}</span>
                  {t(`kiosk.assist.how_${n}`)}
                </li>
              ))}
            </ol>
          </div>
          <RadioGroup value={service} onChange={setService} aria-label={t("kiosk.assist.aid_label")} className="grid gap-3 sm:grid-cols-2">
            <RadioCard value="wheelchair" title={t("kiosk.assist.aid.wheelchair")} />
            <RadioCard value="mobility_escort" title={t("kiosk.assist.aid.mobility_escort")} />
          </RadioGroup>
          <div className="flex flex-col gap-3">
            <CheckboxCard isSelected={stay} onChange={setStay} title={t("kiosk.assist.stay")} />
            <CheckboxCard isSelected={consent} onChange={setConsent} title={t("kiosk.assist.consent")} />
          </div>
          {error && <p role="alert" className="text-body-medium text-text-error-primary">{error}</p>}
        </ModalBody>
        <ModalFooter>
          <Button variant="secondary" className="h-12 rounded-lg px-6" onClick={onClose}>{t("ui.cancel")}</Button>
          <Button variant="primary" leadingIcon={RiCustomerService2Line} className="workspace-primary h-14 rounded-lg px-6"
            disabled={!stay || !consent || busy}
            onClick={async () => { setBusy(true); try { await onSubmit(service); } finally { setBusy(false); } }}>
            {t("ui.submit_request")}
          </Button>
        </ModalFooter>
      </ModalDialog>
    </Modal>
  );
}
