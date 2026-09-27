/**
 * Every address an agent wrote (a catalyst's source, a tail risk's, a holding
 * driver's, a line in a series' source string) goes through here before it
 * becomes a link.
 *
 * The desk checks for `https`, but the page does not trust a check it cannot
 * see: a `javascript:` or `data:` address in an `href` runs in the operator's
 * session when clicked. So an address is a link only when the browser's own
 * URL parser reads it as http or https, and plain text otherwise.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (url === null || url === undefined) return null;
  const trimmed = url.trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  // The address as written: it parsed as http or https, and the browser will
  // resolve the same string the same way.
  return trimmed;
}
