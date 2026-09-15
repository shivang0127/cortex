"use client";

import { useEffect, useRef } from "react";

type Props = {
  open: boolean;
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * An in-app confirmation built on the native <dialog>: `showModal()` gives focus
 * trapping, Escape-to-cancel, an inert page behind it and focus return to the
 * trigger without any library. Cancel receives initial focus so that Enter never
 * confirms a destructive action by accident.
 */
export function ConfirmDialog({ open, title, children, confirmLabel, busy, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby="confirm-title"
      onCancel={(e) => {
        e.preventDefault(); // Escape: route through our handler so state stays in sync
        if (!busy) onCancel();
      }}
      onClick={(e) => {
        if (e.target === ref.current && !busy) onCancel(); // click on the backdrop
      }}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-border bg-panel p-0 text-foreground shadow-xl backdrop:bg-black/40"
    >
      <div className="px-5 py-4">
        <h2 id="confirm-title" className="font-medium">
          {title}
        </h2>
        <div className="mt-2 text-sm text-muted">{children}</div>
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
        <button
          ref={cancelRef}
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded border border-border px-3 py-1.5 text-sm hover:bg-background"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="rounded bg-bad px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Deleting…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
