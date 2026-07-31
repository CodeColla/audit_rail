import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../lib/ui";

/** 0 -> A, 25 -> Z, 26 -> AA — mirrors `api/render.py`'s `_col_letter`. */
function colLetter(index: number): string {
  let letters = "";
  let i = index + 1;
  while (i > 0) {
    const rem = (i - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    i = Math.floor((i - 1) / 26);
  }
  return letters;
}

/**
 * Read-only render of a SHEET version's stored JSON — mirrors `api/render.py`'s
 * `sheet_json_to_html` (same address scheme, same bold/align-only scope) but client-side, so
 * the Content tab needs no server round trip beyond the version fetch it already makes.
 */
function SheetTable({ content }: { content: string }) {
  let parsed: { data?: unknown; bold?: unknown; align?: unknown } = {};
  try { parsed = JSON.parse(content || "{}"); } catch { /* malformed — render as empty */ }
  const data = Array.isArray(parsed.data) ? (parsed.data as unknown[][]) : [];
  const bold = new Set(Array.isArray(parsed.bold) ? (parsed.bold as string[]) : []);
  const align = (parsed.align && typeof parsed.align === "object"
    ? parsed.align : {}) as Record<string, string>;
  if (data.length === 0) return <p className="text-[13px] text-txt3">(empty sheet)</p>;
  return (
    <table className="w-full border-collapse text-[13px]">
      <tbody>
        {data.map((row, r) => (
          <tr key={r}>
            {(Array.isArray(row) ? row : []).map((cell, c) => {
              const addr = `${colLetter(c)}${r + 1}`;
              const a = align[addr];
              return (
                <td key={c} className="border border-bd px-2 py-1"
                  style={{ textAlign: (a as any) || undefined,
                    fontWeight: bold.has(addr) ? 700 : undefined }}>
                  {cell == null ? "" : String(cell)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Renders a document version's body, in whichever format it was authored (P4-S4, P5-S2).
 *
 * Versions written before the rich editor hold markdown; ones written since hold HTML;
 * spreadsheet documents (P5-S2) hold a JSON grid. The branch is required, not cosmetic:
 * react-markdown escapes raw HTML, so an HTML version fed to it displays its own tags as
 * literal text, and the SHEET JSON is not markup at all.
 *
 * The HTML branch is `dangerouslySetInnerHTML`, which is safe here for one reason only —
 * every write path runs the content through `api/html_sanitize.py` server-side. The editor
 * is not the boundary; the API is. Do not render HTML from anywhere that skips it.
 */
export function DocBody({ content, format, className }: {
  content: string;
  format?: "MARKDOWN" | "HTML" | "SHEET" | null;
  className?: string;
}) {
  // `doc-md` on ALL branches — it is the stylesheet hook and the e2e selector
  if (format === "HTML") {
    return <div className={cn("doc-md", className)} dangerouslySetInnerHTML={{ __html: content }} />;
  }
  if (format === "SHEET") {
    return <div className={cn("doc-md", className)}><SheetTable content={content} /></div>;
  }
  return (
    <div className={cn("doc-md", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "*(empty)*"}</ReactMarkdown>
    </div>
  );
}
