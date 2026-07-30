import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../lib/ui";

/**
 * Renders a document version's body, in whichever format it was authored (P4-S4).
 *
 * Versions written before the rich editor hold markdown; ones written since hold HTML. The
 * branch is required, not cosmetic: react-markdown escapes raw HTML, so an HTML version fed
 * to it displays its own tags as literal text.
 *
 * The HTML branch is `dangerouslySetInnerHTML`, which is safe here for one reason only —
 * every write path runs the content through `api/html_sanitize.py` server-side. The editor
 * is not the boundary; the API is. Do not render HTML from anywhere that skips it.
 */
export function DocBody({ content, format, className }: {
  content: string;
  format?: "MARKDOWN" | "HTML" | null;
  className?: string;
}) {
  // `doc-md` on BOTH branches — it is the stylesheet hook and the e2e selector
  if (format === "HTML") {
    return <div className={cn("doc-md", className)} dangerouslySetInnerHTML={{ __html: content }} />;
  }
  return (
    <div className={cn("doc-md", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "*(empty)*"}</ReactMarkdown>
    </div>
  );
}
