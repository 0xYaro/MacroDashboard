import { safeHref } from "@/lib/links";

/**
 * A row's source, small and out of the way, opening in a new tab. An address
 * that is not http or https is shown as text, never linked.
 */
export function SourceLink({ url }: { url: string | null }) {
  if (url === null) return null;
  const href = safeHref(url);
  if (href === null) {
    return <span className="source-link source-link-plain">{url}</span>;
  }
  const host = new URL(href).hostname.replace(/^www\./, "");
  return (
    <a
      className="source-link"
      href={href}
      rel="noreferrer"
      target="_blank"
      title={href}
    >
      {host} ↗
    </a>
  );
}
