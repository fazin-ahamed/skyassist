"use client";

import { useState } from "react";
import { RiSearchLine } from "@remixicon/react";
import { Modal, ModalBody, ModalDialog, ModalHeader } from "@/components/base/modal/modal";
import { SearchField } from "@/components/base/search-field/search-field";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { Board, rowsOf } from "@/components/kiosk/board";
import { useBoard } from "@/hooks/use-board";
import { useI18n } from "@/lib/i18n";

type Direction = "departures" | "arrivals";

/** The live board as a picker: choose a flight and it fills the matching field on the start tag. */
export function BrowseModal({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick?: (code: string, dir: Direction) => void }) {
  const { t } = useI18n();
  const [dir, setDir] = useState<Direction>("departures");
  const [query, setQuery] = useState("");
  const { board, failed, retry } = useBoard(dir);
  const needle = query.trim().toUpperCase().replace(/\s/g, "");
  const rows = board ? rowsOf(board, dir).filter((row) =>
    (row.code.replace(/\s/g, "") + row.city).toUpperCase().includes(needle)) : null;
  const select = onPick ? (id: string) => {
    const row = rows?.find((entry) => entry.id === id);
    if (row) onPick(row.code, dir);
  } : undefined;
  return (
    <Modal isOpen={open} onOpenChange={(o) => !o && onClose()} size="lg">
      <ModalDialog className="paper">
        <ModalHeader title={t("agent.browse")} icon={RiSearchLine} />
        <ModalBody className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <SegmentedControl aria-label={t("agent.browse")} selectedKeys={[dir]} onSelectionChange={(k) => { const v = [...k][0]; if (v) setDir(v as Direction); }}>
              <SegmentedControlItem id="departures">{t("kiosk.board.departures")}</SegmentedControlItem>
              <SegmentedControlItem id="arrivals">{t("kiosk.board.arrivals")}</SegmentedControlItem>
            </SegmentedControl>
            <SearchField className="min-w-36 flex-1" aria-label={t("kiosk.search_placeholder")} placeholder={t("kiosk.search_placeholder")} value={query} onChange={setQuery} autoComplete="off" />
          </div>
          <Board rows={rows} failed={failed} retry={retry} selected={null} onSelect={select} />
        </ModalBody>
      </ModalDialog>
    </Modal>
  );
}
