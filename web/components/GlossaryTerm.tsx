"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { GlossaryEntry } from "@/lib/glossary";

/**
 * The popover's width, and the gap it keeps from the viewport's edges. The
 * width is also in the stylesheet (`.gloss-pop`), so the height measured for
 * placement is the height it will have.
 */
const POP_WIDTH = 288;
const EDGE = 8;

/**
 * A term the page keeps, with its plain meaning one hover, focus or tap away.
 *
 * The term is a button so a keyboard reaches it and a screen reader announces
 * the definition as its description. It opens on hover with a mouse, on focus
 * from the keyboard, and toggles on a tap, so it works on a phone where there
 * is no hover. Escape closes it, and so does a tap anywhere else. The pointer
 * can move onto the popover without it closing, focus leaving the term closes
 * it, and only one is open on the page at a time.
 *
 * The popover is `position: fixed` and placed from the term's own rectangle.
 * The scenario table scrolls sideways on a phone, and a popover positioned
 * inside that scroller would be clipped by it. Fixed placement also keeps the
 * popover out of layout, so it can never widen the page.
 *
 * With no entry the children render as they are, so a caller can wrap every
 * label and let the glossary decide which ones get a definition.
 */
export function GlossaryTerm({
  entry,
  heading,
  children,
}: {
  entry: GlossaryEntry | null;
  /**
   * A line the popover leads with, for a label the row shows only part of.
   * Without it the popover is the definition alone: the term is already on
   * screen, and printing it twice made every label findable twice.
   */
  heading?: string;
  children: ReactNode;
}) {
  if (entry === null) return <>{children}</>;
  return (
    <Term entry={entry} heading={heading ?? null}>
      {children}
    </Term>
  );
}

/**
 * Only one popover is open on the page at a time. Each open term registers its
 * close here, and opening another closes the last one first, so a keyboard
 * user tabbing along a row never builds a stack of definitions.
 */
let closeOpenTerm: (() => void) | null = null;

/** How long a popover waits after the pointer leaves, so it can be reached. */
const LEAVE_GRACE_MS = 150;

function Term({
  entry,
  heading,
  children,
}: {
  entry: GlossaryEntry;
  heading: string | null;
  children: ReactNode;
}) {
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(
    null,
  );
  const open = hover || focus || pinned;

  const cancelLeave = useCallback(() => {
    if (leaveTimer.current !== null) {
      clearTimeout(leaveTimer.current);
      leaveTimer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    cancelLeave();
    setHover(false);
    setFocus(false);
    setPinned(false);
  }, [cancelLeave]);

  // The pointer left the term or the popover. Wait a moment before closing,
  // so it can cross the gap between the two (WCAG 1.4.13, hoverable).
  const leave = useCallback(() => {
    cancelLeave();
    leaveTimer.current = setTimeout(() => {
      leaveTimer.current = null;
      setHover(false);
    }, LEAVE_GRACE_MS);
  }, [cancelLeave]);

  const enter = useCallback(() => {
    cancelLeave();
    setHover(true);
  }, [cancelLeave]);

  const measure = useCallback(() => {
    const rect = button.current?.getBoundingClientRect();
    if (rect === undefined) return;
    const width = Math.min(POP_WIDTH, window.innerWidth - EDGE * 2);
    const left = Math.max(
      EDGE,
      Math.min(rect.left, window.innerWidth - width - EDGE),
    );
    // Below the term by default, above it when the bottom of the viewport
    // would cut it off and there is room above.
    const height = pop.current?.offsetHeight ?? 0;
    const below = rect.bottom + 6;
    const above = rect.top - 6 - height;
    const flip = below + height > window.innerHeight - EDGE && above >= EDGE;
    setPlace({ top: flip ? above : below, left });
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  // One open at a time: this one takes the slot and closes whoever held it.
  useLayoutEffect(() => {
    if (!open) return;
    if (closeOpenTerm !== null && closeOpenTerm !== close) closeOpenTerm();
    closeOpenTerm = close;
    return () => {
      if (closeOpenTerm === close) closeOpenTerm = null;
    };
  }, [open, close]);

  useEffect(() => cancelLeave, [cancelLeave]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    // A tap outside closes a pinned popover. Pointer-down rather than click,
    // so the tap that opens another term closes this one first. A tap on the
    // popover itself is inside, so a reader can select its text.
    const onPointer = (event: PointerEvent) => {
      if (!wrap.current?.contains(event.target as Node)) close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, close, measure]);

  return (
    <span className="gloss" ref={wrap}>
      <button
        aria-describedby={id}
        className="gloss-term"
        onBlur={(event) => {
          // Focus leaving the term takes a keyboard pin with it; a pin that
          // outlived its focus was how popovers came to stack.
          if (!wrap.current?.contains(event.relatedTarget as Node | null)) {
            setFocus(false);
            setPinned(false);
          }
        }}
        onClick={() => setPinned((was) => !was)}
        onFocus={(event) => {
          // Keyboard focus opens it. Focus that comes with a tap does not,
          // or the second tap could never close what the first one opened.
          setFocus(focusVisible(event.currentTarget));
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse") enter();
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse") leave();
        }}
        ref={button}
        type="button"
      >
        {children}
      </button>
      <span
        className="gloss-pop"
        hidden={!open}
        id={id}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse") enter();
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse") leave();
        }}
        ref={pop}
        role="tooltip"
        style={
          place === null ? undefined : { top: place.top, left: place.left }
        }
      >
        {heading === null ? null : <b className="gloss-head">{heading}</b>}
        {entry.definition}
      </span>
    </span>
  );
}

function focusVisible(element: Element): boolean {
  try {
    return element.matches(":focus-visible");
  } catch {
    // An engine without the selector gets the accessible default: open.
    return true;
  }
}
