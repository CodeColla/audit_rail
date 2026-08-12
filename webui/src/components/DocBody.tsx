import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../lib/ui";
import { formatCell } from "../lib/sheetFormat";
import { fetchBlob } from "../lib/api";

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

/** One worksheet's cell formatting, normalised from either stored shape. */
type CellStyle = {
  bold?: boolean; italic?: boolean; underline?: boolean;
  align?: string; fontSize?: number; color?: string; background?: string;
};
type Worksheet = {
  name: string;
  data: unknown[][];
  style: Record<string, CellStyle>;
  merges: Record<string, { colspan?: number; rowspan?: number }>;
  /** Per-COLUMN text wrapping — see api/render.py's colWrap note for why it is not per cell. */
  colWrap: boolean[];
  /** Per-COLUMN number format — "currency:INR" | "percent" | "". Same per-column reasoning,
   *  plus jspreadsheet only exposes formatting at column level. See lib/sheetFormat.ts. */
  colFormat: string[];
  /** Per-CELL reviewer notes (P6-S5b). */
  comments: Record<string, string>;
};

/**
 * Normalise a stored SHEET body into worksheets — the client-side twin of `parse_sheet` in
 * `api/render.py`, and it MUST stay in step with it.
 *
 * Handles both shapes on purpose. v1 (`{data, bold, align}`) is not a migration window that
 * can be closed: `freeze_published_version()` makes a published version's `content`
 * immutable and `content_sha256` (which backs `electronic_signatures`) is generated from it,
 * so a v1 sheet that was ever published can never be rewritten. This reader is permanent.
 */
function readSheets(content: string): Worksheet[] {
  let parsed: any = {};
  try { parsed = JSON.parse(content || "{}"); } catch { /* malformed — render as empty */ }
  if (!parsed || typeof parsed !== "object") return [];

  if (parsed.version === 2 && Array.isArray(parsed.sheets)) {
    return parsed.sheets.map((s: any, i: number) => ({
      name: String(s?.name ?? `Sheet${i + 1}`),
      data: Array.isArray(s?.data) ? s.data : [],
      style: (s?.style && typeof s.style === "object" ? s.style : {}) as Record<string, CellStyle>,
      merges: (s?.merges && typeof s.merges === "object" ? s.merges : {}),
      colWrap: Array.isArray(s?.colWrap) ? s.colWrap : [],
      colFormat: Array.isArray(s?.colFormat) ? s.colFormat : [],
      comments: (s?.comments && typeof s.comments === "object" ? s.comments : {}),
    }));
  }

  // v1 → the same structure, so everything below has exactly one shape to render.
  const style: Record<string, CellStyle> = {};
  for (const addr of Array.isArray(parsed.bold) ? parsed.bold : []) {
    style[addr] = { ...style[addr], bold: true };
  }
  const align = parsed.align && typeof parsed.align === "object" ? parsed.align : {};
  for (const [addr, a] of Object.entries(align)) {
    style[addr] = { ...style[addr], align: String(a) };
  }
  return [{ name: "Sheet1", data: Array.isArray(parsed.data) ? parsed.data : [],
            style, merges: {}, colWrap: [], colFormat: [], comments: {} }];
}

function SheetGrid({ sheet }: { sheet: Worksheet }) {
  // A merged cell's neighbours must not also be rendered, or the row grows by the width of
  // every span. Mirrors the `taken` bookkeeping in docx_export._table().
  const covered = new Set<string>();
  for (const [addr, span] of Object.entries(sheet.merges)) {
    const m = /^([A-Z]+)(\d+)$/.exec(addr);
    if (!m) continue;
    const c0 = m[1].split("").reduce((a, ch) => a * 26 + (ch.charCodeAt(0) - 64), 0) - 1;
    const r0 = parseInt(m[2], 10) - 1;
    for (let r = r0; r < r0 + (span.rowspan ?? 1); r++) {
      for (let c = c0; c < c0 + (span.colspan ?? 1); c++) {
        if (r !== r0 || c !== c0) covered.add(`${r}:${c}`);
      }
    }
  }
  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {sheet.data.map((row, r) => (
          <tr key={r}>
            {(Array.isArray(row) ? row : []).map((cell, c) => {
              if (covered.has(`${r}:${c}`)) return null;
              const addr = `${colLetter(c)}${r + 1}`;
              const s = sheet.style[addr] ?? {};
              const span = sheet.merges[addr];
              const note = sheet.comments[addr];
              return (
                <td key={c} title={note || undefined}
                  className={cn("relative border border-bd px-2 py-1",
                                note && "doc-sheet-comment")}
                  colSpan={span?.colspan && span.colspan > 1 ? span.colspan : undefined}
                  rowSpan={span?.rowspan && span.rowspan > 1 ? span.rowspan : undefined}
                  style={{
                    textAlign: (s.align as any) || undefined,
                    fontWeight: s.bold ? 700 : undefined,
                    fontStyle: s.italic ? "italic" : undefined,
                    textDecoration: s.underline ? "underline" : undefined,
                    fontSize: s.fontSize ? `${s.fontSize}pt` : undefined,
                    color: s.color || undefined,
                    backgroundColor: s.background || undefined,
                    whiteSpace: sheet.colWrap[c] ? "pre-wrap" : undefined,
                  }}>
                  {/* Formatted for DISPLAY only — the stored cell keeps the raw number, and
                      `formatCell` leaves non-numeric cells (a column's header) alone. */}
                  {formatCell(cell == null ? "" : String(cell), sheet.colFormat[c] ?? "")}
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
 * Read-only render of a SHEET version — the client-side mirror of `api/render.py`'s
 * `sheet_json_to_html`, so the Content tab and the public signing page need no server round
 * trip beyond the version fetch they already make.
 *
 * A workbook shows a heading per worksheet, matching how the PDF paginates them. A
 * single-sheet document gets no heading, so it looks exactly as it did before multi-sheet
 * support existed.
 */
function SheetTable({ content }: { content: string }) {
  const sheets = readSheets(content);
  const empty = sheets.every((s) => s.data.length === 0);
  if (sheets.length === 0 || empty) {
    return <p className="text-sm text-txt3">(empty sheet)</p>;
  }
  if (sheets.length === 1) return <SheetGrid sheet={sheets[0]} />;
  return (
    <>
      {sheets.map((s, i) => (
        <section key={i} className={i ? "mt-6" : undefined}>
          <h2 className="mb-2 text-body font-semibold">{s.name}</h2>
          <SheetGrid sheet={s} />
        </section>
      ))}
    </>
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
/** `src="/api/documents/images/…"` -> `data-doc-image="…"`, before the HTML is ever inserted.
 *
 *  This has to happen to the STRING, not in an effect afterwards. `dangerouslySetInnerHTML`
 *  starts the browser fetching every image the instant the markup is parsed — long before any
 *  `useEffect` could swap the src — and those requests carry no bearer token, so each one is a
 *  401 in the console. `01-smoke.spec.ts` asserts that no `/api/` response is ever >= 400 on
 *  any route, and would rightly fail. Stripping the src first means the browser never issues
 *  a request we know is doomed.
 *
 *  Only our own URL shape is touched. The signing page's srcs already point at the
 *  token-scoped public route, which needs no bearer token and must be left exactly alone. */
const _SRC_TO_DATA = new RegExp(
  'src="/api/documents/images/'
  + "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\"", "g");

const deferImages = (html: string) => html.replace(_SRC_TO_DATA, 'data-doc-image="$1"');

/** Fetch each deferred image with the bearer token and fill in an object URL. */
function useDeferredImages(ref: React.RefObject<HTMLDivElement | null>, content: string) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    let live = true;
    const revokers: Array<() => void> = [];
    root.querySelectorAll<HTMLImageElement>("img[data-doc-image]").forEach((img) => {
      const id = img.dataset.docImage;
      if (!id) return;
      fetchBlob(`/documents/images/${id}`)
        .then((got) => {
          if (!live) { got.revoke(); return; }
          revokers.push(got.revoke);
          img.src = got.objectUrl;
        })
        .catch(() => { if (live) img.alt = img.alt || "image unavailable"; });
    });
    return () => { live = false; revokers.forEach((r) => r()); };
  }, [ref, content]);
}

export function DocBody({ content, format, className }: {
  content: string;
  format?: "MARKDOWN" | "HTML" | "SHEET" | null;
  className?: string;
}) {
  const htmlRef = useRef<HTMLDivElement>(null);
  useDeferredImages(htmlRef, format === "HTML" ? content : "");

  // `doc-md` on ALL branches — it is the stylesheet hook and the e2e selector
  if (format === "HTML") {
    return <div ref={htmlRef} className={cn("doc-md", className)}
      dangerouslySetInnerHTML={{ __html: deferImages(content) }} />;
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
