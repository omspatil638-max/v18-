import { useEffect } from "react";

/** Everything a keyboard user can Tab to inside a dialog. */
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Focus management for a modal: on mount, focus `initialRef` (or leave focus alone when it is not set);
 * on unmount, give focus back to whatever had it before (if it is still on the page).
 */
export function useDialogFocus(initialRef?: React.RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    initialRef?.current?.focus();
    return () => {
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
    // Runs once per open: the dialog is mounted only while it is showing.
  }, []);
}

/** Keep Tab / Shift+Tab inside `container`. Call from the container's onKeyDown. */
export function trapTabKey(e: React.KeyboardEvent, container: HTMLElement | null): void {
  if (e.key !== "Tab" || !container) return;
  const nodes = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (nodes.length === 0) {
    e.preventDefault();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && (active === first || active === container)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}
