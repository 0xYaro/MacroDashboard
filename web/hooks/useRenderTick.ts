"use client";

import { useEffect, useState } from "react";

/**
 * Force a re-render on a cadence, so an age label stays true without new data.
 *
 * "4h ago" is a statement about now, not about the payload, and a panel that
 * only repaints when a row changes would print it for as long as the terminal
 * sat still. This matters more here than on a streaming book, not less: the
 * data behind this app moves twice a day, so a page left open all afternoon
 * gets no new row at all and the age is the only thing on screen that changes.
 *
 * **The clock is sampled at render, never carried on the tick.** A timestamp
 * captured when the interval fired sits a fraction behind a response that
 * landed immediately after, and the label then reads as a receipt from the
 * future. So this hook returns nothing: it only makes the component ask
 * `new Date()` again.
 */
export function useRenderTick(intervalMs = 1000): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const handle = setInterval(() => setTick((value) => value + 1), intervalMs);
    return () => clearInterval(handle);
  }, [intervalMs]);
}
