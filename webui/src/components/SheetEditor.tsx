import { useEffect, useRef, useState } from "react";
import {
  CURRENCY_SYMBOLS, FORMAT_OPTIONS, NUMERIC_RE, formatCell, unformatCell,
} from "../lib/sheetFormat";

/**
 * The spreadsheet-document authoring surface, parallel to `RichTextEditor` — same
 * `{value, onChange}` contract, so `DocumentDetail.tsx` dispatches between the two purely on
 * `content_format` without either editor knowing the other exists.
 *
 * `value`/`onChange` carry a JSON STRING, not markup — the v2 workbook shape that
 * `api/render.py`'s `parse_sheet` validates server-side:
 *
 *   {"version": 2, "sheets": [{ name, data, formulas, style, merges, colWidths }]}
 *
 * Two things about that format are load-bearing and easy to break:
 *
 *  1. **`data` holds COMPUTED values; `formulas` holds the source.** The PDF and DOCX
 *     exports are Python and have no formula engine, so if only `=SUM(A1:A9)` were stored
 *     every export would print the expression instead of the number. Storing the evaluated
 *     result is also what makes a published sheet honest: it is frozen at publish, so
 *     `TODAY()` renders forever as the date the version was approved rather than the
 *     reader's today, and a signed document always shows the numbers its approvers signed.
 *
 *  2. **The format is ours, not jspreadsheet's.** `getConfig()` would happily hand over the
 *     library's entire internal options object, and persisting that would weld frozen,
 *     signed compliance records to one vendor's schema. Everything below maps explicitly to
 *     our own documented shape instead — see docs/phase5/04-spreadsheet-library-evaluation.md.
 *
 * jspreadsheet-ce is loaded via dynamic `import()`, matching how `docx-preview`/`xlsx` are
 * already handled in `FilePreview.tsx`, so neither the library (~490kB) nor the Material
 * Icons webfont (~126kB) reaches a user who never opens a spreadsheet document.
 *
 * Mounts the widget ONCE and treats it as effectively uncontrolled after that — `value` only
 * ever changes here as an ECHO of this component's own `onChange` (the parent just stores
 * whatever was last reported), so re-syncing external `value` changes into a live instance
 * would be pointless work that could also disrupt whatever cell is being edited. `Editor` in
 * DocumentDetail.tsx already gates mounting on the version's data being loaded, so there is
 * no "content arrives after mount" case either, unlike `RichTextEditor`.
 */

const ALIGNMENTS = new Set(["left", "center", "right"]);
/** The number-format picker's id and accessible name.
 *
 *  A `tooltip` would be the obvious way to name it and does not work: jspreadsheet renames a
 *  toolbar item's `tooltip` to `title`, and jSuites only turns THAT into an attribute for
 *  ICON items — a `select` comes out as a bare `role="combobox"` with no accessible name,
 *  unusable by a screen reader and unaddressable by a spec. `id` is set before jSuites
 *  branches on item type, so it survives; the label is applied in `updateState`, the only
 *  callback holding the element (the toolbar is built asynchronously, so querying for it
 *  after the constructor returns finds nothing). Both read out of the libraries' sources. */
const FORMAT_ITEM_ID = "sheet-number-format";
const FORMAT_ITEM_LABEL = "Number format for this column";
/** Mirrors `render.MIN_FONT_PT`/`MAX_FONT_PT` — the server rejects anything outside. */
const MIN_FONT_PT = 6, MAX_FONT_PT = 72;

type CellStyle = {
  bold?: boolean; italic?: boolean; underline?: boolean;
  align?: string; fontSize?: number; color?: string; background?: string;
};
type StoredSheet = {
  name: string;
  data: string[][];
  formulas: Record<string, string>;
  style: Record<string, CellStyle>;
  merges: Record<string, { colspan: number; rowspan: number }>;
  colWidths: number[];
  /** Per-COLUMN wrap. Deliberately not per cell: a per-cell `white-space` set via setStyle is
   *  wiped by jspreadsheet's own updateCell on the next value change (verified in a browser),
   *  whereas `columns[i].wordWrap` is consulted on every render and survives editing. */
  colWrap: boolean[];
  /** Per-COLUMN number format — "" | "percent" | "currency:INR" (P6-S4). Per column for the
   *  same reason, and because jspreadsheet exposes formatting nowhere else: its `render` hook
   *  takes the COLUMN config as an argument. See lib/sheetFormat.ts. */
  colFormat: string[];
  /** Per-CELL reviewer notes (P6-S5b), `{A1: "why this figure is an exception"}`. Per cell,
   *  unlike wrap and format, because a note is about one cell and jspreadsheet's comment API
   *  is per cell too. */
  comments: Record<string, string>;
};

/**
 * Format one already-rendered cell in place, stashing the raw value it displaced.
 *
 * Called from the column `render` hook, which jspreadsheet invokes immediately after it has
 * written the cell's own value into `textContent` — so the text found here is the RAW value
 * (the computed result for a formula), at full precision, and the stash is exact rather than
 * a re-parse of formatted text.
 *
 * That stash is load-bearing on save: `getData(processed)` reads `element.innerHTML`, so once
 * a cell displays "₹1,234.57" that is what the naive save path would store. `emit` reads
 * `dataset.raw` instead and keeps the number.
 */
function formatInPlace(cell: HTMLElement, fmt: string) {
  const raw = cell.textContent ?? "";
  if (!fmt || !NUMERIC_RE.test(raw)) return;
  cell.dataset.raw = raw;
  cell.textContent = formatCell(raw, fmt);
}

/** jspreadsheet's per-column display hook. ONE function serves every column — it reads the
 *  format off the column config it is handed, so changing a column's format needs no rebind.
 *  Non-numeric cells are left untouched, which is why this exists instead of the library's
 *  `mask` option: a mask renders the header "Amount" as a bare "₹" (verified against
 *  jsuites' `mask.render`), silently destroying the top row of any register. */
function renderFormattedCell(
  cell: HTMLElement | null, _value: unknown, _x: number, _y: number,
  _instance: unknown, colOpts: any,
) {
  if (!cell) return;
  // jspreadsheet has just rewritten textContent, so any previous stash describes a value
  // that is no longer there.
  delete cell.dataset.raw;
  formatInPlace(cell, String(colOpts?.numberFormat ?? ""));
}

/** Re-format a cell that is already on screen, for the toolbar path — here the STASH is the
 *  authoritative raw value, because `textContent` currently holds our own formatted text. */
function reformatInPlace(cell: HTMLElement, fmt: string) {
  const raw = cell.dataset.raw;
  if (raw !== undefined) {
    cell.textContent = raw;
    delete cell.dataset.raw;
  }
  formatInPlace(cell, fmt);
}

/** One reviewer note, flattened across worksheets for the panel. */
type CommentEntry = { sheet: string; sheetIndex: number; cell: string; note: string };

/** Every comment in the workbook, in reading order.
 *
 *  Reads the LIVE worksheet list rather than the array captured at mount, for the same reason
 *  `emit` does: a worksheet added through the tab bar is a new instance the original array
 *  never sees. */
function collectComments(sheets: any[] | null): CommentEntry[] {
  if (!sheets?.length) return [];
  const parent = (sheets[0] as any)?.parent;
  const live = (parent?.worksheets ?? sheets) as any[];
  const out: CommentEntry[] = [];
  live.forEach((inst, i) => {
    const found = (inst?.getComments?.() ?? {}) as Record<string, string>;
    const name = String(inst?.options?.worksheetName ?? `Sheet${i + 1}`);
    for (const [cell, note] of Object.entries(found)) {
      if (note) out.push({ sheet: name, sheetIndex: i, cell, note });
    }
  });
  return out.sort((a, b) => a.sheetIndex - b.sheetIndex || a.cell.localeCompare(b.cell));
}

/** "rgb(1, 2, 3)" | "#abc" | "#aabbcc" -> "#aabbcc", or null if it isn't a colour we store.
 *  The server only accepts 3/6-digit hex (`render._COLOUR_RE`), and browsers report computed
 *  colours as rgb(), so this normalisation is required rather than cosmetic. */
function toHex(raw: string | undefined): string | undefined {
  if (!raw) return undefined;
  const v = raw.trim();
  const rgb = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(v);
  if (rgb) {
    const h = (n: string) => Math.max(0, Math.min(255, parseInt(n, 10))).toString(16).padStart(2, "0");
    return `#${h(rgb[1])}${h(rgb[2])}${h(rgb[3])}`;
  }
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v);
  if (!hex) return undefined;
  const body = hex[1];
  return `#${(body.length === 3 ? body.split("").map((c) => c + c).join("") : body).toLowerCase()}`;
}

/** jspreadsheet stores per-cell style as a raw CSS string. Pull out only the properties our
 *  format defines — anything else the toolbar can set (borders, vertical-align, font family)
 *  is deliberately not persisted, and the server would reject it anyway. */
function cssToStyle(css: string): CellStyle | null {
  const s: CellStyle = {};
  if (/font-weight\s*:\s*(bold|[6-9]00)/i.test(css)) s.bold = true;
  if (/font-style\s*:\s*italic/i.test(css)) s.italic = true;
  if (/text-decoration[^;]*:\s*[^;]*underline/i.test(css)) s.underline = true;

  const align = /text-align\s*:\s*(left|center|right)/i.exec(css);
  // "left" is the default (see defaultColAlign) — recording it would mark every untouched
  // cell as explicitly aligned and bloat the stored JSON for no visual difference.
  if (align && align[1].toLowerCase() !== "left") s.align = align[1].toLowerCase();

  const size = /font-size\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt)/i.exec(css);
  if (size) {
    const n = parseFloat(size[1]);
    const pt = Math.round(size[2].toLowerCase() === "px" ? n * 0.75 : n);
    if (pt >= MIN_FONT_PT && pt <= MAX_FONT_PT) s.fontSize = pt;
  }

  const colour = /(?:^|;)\s*color\s*:\s*([^;]+)/i.exec(css);
  if (colour) { const h = toHex(colour[1]); if (h) s.color = h; }
  const bg = /background(?:-color)?\s*:\s*([^;]+)/i.exec(css);
  if (bg) { const h = toHex(bg[1]); if (h) s.background = h; }

  return Object.keys(s).length ? s : null;
}

/**
 * Drop trailing all-blank rows and columns before saving.
 *
 * The editor always presents at least a 12x60 grid (`minDimensions`, sized so the sheet fills
 * a fullscreen window), so without this every document would persist ~720 cells of `""` —
 * bloating stored bytes, the version diff and the .xlsx export for content nobody entered.
 * `minDimensions` is a floor, so a trimmed sheet still reopens at full size.
 *
 * A row/column is only removable if it is blank in EVERY sense: no value, and no formula,
 * style or merge anchored anywhere in it. Trimming a styled-but-empty cell would silently
 * discard formatting the user applied deliberately.
 */
function trimBlankEdges(
  data: string[][],
  formulas: Record<string, string>,
  style: Record<string, CellStyle>,
  merges: Record<string, { colspan: number; rowspan: number }>,
): string[][] {
  const decorated = new Set([...Object.keys(formulas), ...Object.keys(style), ...Object.keys(merges)]);
  const rowsWithMeta = new Set<number>();
  const colsWithMeta = new Set<number>();
  for (const addr of decorated) {
    const m = /^([A-Z]+)(\d+)$/.exec(addr);
    if (!m) continue;
    rowsWithMeta.add(parseInt(m[2], 10) - 1);
    colsWithMeta.add(m[1].split("").reduce((a, ch) => a * 26 + (ch.charCodeAt(0) - 64), 0) - 1);
  }

  // The two axes must be computed INDEPENDENTLY. Testing `hasValue || rowMeta || colMeta`
  // per cell and extending both bounds together means one styled column marks every row as
  // occupied (and one styled row marks every column), so a single right-aligned cell
  // defeated the trim entirely and the sheet saved as the full 20x60 grid.
  let lastRow = -1, lastCol = -1;
  data.forEach((row, r) => row.forEach((cell, c) => {
    if (cell === "") return;
    if (r > lastRow) lastRow = r;
    if (c > lastCol) lastCol = c;
  }));
  for (const r of rowsWithMeta) if (r > lastRow) lastRow = r;
  for (const c of colsWithMeta) if (c > lastCol) lastCol = c;
  if (lastRow < 0 || lastCol < 0) return [];        // a completely untouched sheet
  return data.slice(0, lastRow + 1).map((row) => row.slice(0, lastCol + 1));
}

/** `[true,false,false]` -> `[true]`, and `["","percent",""]` -> `["","percent"]`. Positional,
 *  so dropping only the TAIL cannot shift any column's meaning. Both per-column arrays are
 *  trimmed for the same reason blank rows are: an untouched 20-column grid would otherwise
 *  store 20 entries of nothing in every document and every version diff. */
function dropTrailingEmpty<T extends boolean | string>(flags: T[], blank: T): T[] {
  let last = -1;
  flags.forEach((f, i) => { if (f !== blank) last = i; });
  return last < 0 ? [] : flags.slice(0, last + 1);
}

/** Our stored style -> the CSS string jspreadsheet wants when seeding a worksheet. */
function styleToCss(s: CellStyle): string {
  const d: string[] = [];
  if (s.bold) d.push("font-weight:bold");
  if (s.italic) d.push("font-style:italic");
  if (s.underline) d.push("text-decoration:underline");
  if (s.align && ALIGNMENTS.has(s.align)) d.push(`text-align:${s.align}`);
  if (s.fontSize) d.push(`font-size:${s.fontSize}pt`);
  if (s.color) d.push(`color:${s.color}`);
  if (s.background) d.push(`background-color:${s.background}`);
  return d.join(";");
}

/** Read the stored JSON in either shape. The v1 path is permanent, not a migration window:
 *  a published version's `content` is immutable (freeze trigger) and its hash backs
 *  `electronic_signatures`, so a v1 sheet that was ever published can never be rewritten. */
function readWorkbook(raw: string): StoredSheet[] {
  let parsed: any = {};
  try { parsed = JSON.parse(raw || "{}"); } catch { /* fresh sheet */ }
  if (!parsed || typeof parsed !== "object") parsed = {};

  const blank = (name: string): StoredSheet =>
    ({ name, data: [], formulas: {}, style: {}, merges: {}, colWidths: [], colWrap: [],
       colFormat: [], comments: {} });

  if (parsed.version === 2 && Array.isArray(parsed.sheets) && parsed.sheets.length) {
    return parsed.sheets.map((s: any, i: number) => ({
      ...blank(String(s?.name ?? `Sheet${i + 1}`)),
      data: Array.isArray(s?.data) ? s.data : [],
      formulas: s?.formulas && typeof s.formulas === "object" ? s.formulas : {},
      style: s?.style && typeof s.style === "object" ? s.style : {},
      merges: s?.merges && typeof s.merges === "object" ? s.merges : {},
      colWidths: Array.isArray(s?.colWidths) ? s.colWidths : [],
      colWrap: Array.isArray(s?.colWrap) ? s.colWrap : [],
      colFormat: Array.isArray(s?.colFormat) ? s.colFormat : [],
      comments: s?.comments && typeof s.comments === "object" ? s.comments : {},
    }));
  }

  const sheet = blank("Sheet1");
  sheet.data = Array.isArray(parsed.data) ? parsed.data : [];
  for (const addr of Array.isArray(parsed.bold) ? parsed.bold : []) {
    sheet.style[addr] = { ...sheet.style[addr], bold: true };
  }
  const align = parsed.align && typeof parsed.align === "object" ? parsed.align : {};
  for (const [addr, a] of Object.entries(align)) {
    sheet.style[addr] = { ...sheet.style[addr], align: String(a) };
  }
  return [sheet];
}

/**
 * Read an uploaded .xlsx/.csv into our stored shape, using SheetJS.
 *
 * Jspreadsheet CE cannot read .xlsx (that is a paid tier), but the app already ships SheetJS
 * for evidence previews (`FilePreview.tsx`), so import costs no new dependency and no
 * licence — see docs/phase5/04-spreadsheet-library-evaluation.md.
 *
 * Both the computed value and the formula source are captured: SheetJS exposes `cell.w`
 * (formatted text), `cell.v` (raw value) and `cell.f` (formula), which maps exactly onto the
 * `data` / `formulas` split our format needs.
 */
async function importWorkbook(file: File): Promise<StoredSheet[]> {
  const XLSX = await import("xlsx");
  const wb = XLSX.read(await file.arrayBuffer(), { type: "array", cellStyles: true });
  return wb.SheetNames.map((name) => {
    const ws = wb.Sheets[name];
    const sheet: StoredSheet = {
      name, data: [], formulas: {}, style: {}, merges: {}, colWidths: [], colWrap: [],
      colFormat: [], comments: {},
    };
    const ref = ws["!ref"];
    if (!ref) return sheet;
    const range = XLSX.utils.decode_range(ref);

    // Column formats FIRST, because they change how the values below are read. Without this
    // an imported currency column arrived as the text "₹1,234.50" in `data` — it looked
    // right and was neither sortable, computable, nor re-formattable, which is the exact
    // defect P6-S4 exists to remove.
    for (let c = range.s.c; c <= range.e.c; c++) {
      let fmt = "";
      for (let r = range.s.r; r <= range.e.r && !fmt; r++) {
        const cell = ws[XLSX.utils.encode_cell({ r, c })] as any;
        if (typeof cell?.v === "number") fmt = excelFormatToOurs(cell.z);
      }
      sheet.colFormat[c - range.s.c] = fmt;
    }

    for (let r = range.s.r; r <= range.e.r; r++) {
      const row: string[] = [];
      for (let c = range.s.c; c <= range.e.c; c++) {
        const cell = ws[XLSX.utils.encode_cell({ r, c })] as any;
        // `w` is the formatted text Excel displays; fall back to the raw value. In a
        // FORMATTED column take `v` instead — we render the symbol ourselves, and taking `w`
        // there would both duplicate it and turn the number into text.
        const formatted = sheet.colFormat[c - range.s.c] && typeof cell?.v === "number";
        row.push(cell == null ? "" : String(formatted ? cell.v : (cell.w ?? cell.v ?? "")));
        if (cell?.f) sheet.formulas[`${colLetter(c - range.s.c)}${r - range.s.r + 1}`] = `=${cell.f}`;
      }
      sheet.data.push(row);
    }
    sheet.colFormat = dropTrailingEmpty(sheet.colFormat, "");
    for (const m of (ws["!merges"] ?? []) as any[]) {
      const colspan = m.e.c - m.s.c + 1, rowspan = m.e.r - m.s.r + 1;
      if (colspan > 1 || rowspan > 1) {
        sheet.merges[`${colLetter(m.s.c - range.s.c)}${m.s.r - range.s.r + 1}`] = { colspan, rowspan };
      }
    }
    // Excel column widths are in characters (~7px each); our format stores pixels.
    sheet.colWidths = ((ws["!cols"] ?? []) as any[])
      .map((col) => (col?.wch ? Math.round(col.wch * 7) : 100));
    return sheet;
  });
}

/** An Excel number-format string -> one of ours, or "" for anything we do not model.
 *
 *  Matched on the SYMBOL rather than on Excel's built-in format ids because a real workbook
 *  spells rupees as `[$₹-en-IN]#,##0.00`, `"₹"#,##0.00` or a locale id depending on where it
 *  was written, and all three contain the symbol. Anything unrecognised imports as a plain
 *  number, which is lossless — the author can then pick a format from the toolbar. */
function excelFormatToOurs(z: unknown): string {
  const s = typeof z === "string" ? z : "";
  if (!s) return "";
  if (s.includes("%")) return "percent";
  for (const [code, symbol] of Object.entries(CURRENCY_SYMBOLS)) {
    if (s.includes(symbol)) return `currency:${code}`;
  }
  return "";
}

export function SheetEditor({ value, onChange }: {
  value: string;
  onChange: (json: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const initialValue = useRef(value);   // read once; see the mount-once note above
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const hostSheets = useRef<any[] | null>(null);
  // The formula bar's view of the grid: which cell is anchored, and its SOURCE (a formula
  // where there is one, not the computed value — that is the point of a formula bar).
  const [sel, setSel] = useState<{ x: number; y: number; value: string } | null>(null);
  // Every comment in the workbook, for the panel. Kept in React state rather than read on
  // render because the grid is a mount-once widget and its DOM is not ours to poll.
  const [comments, setComments] = useState<CommentEntry[]>([]);
  const [showComments, setShowComments] = useState(false);
  /** Select the cell a comment belongs to, switching worksheet first if it is on another.
   *  A list of notes you cannot navigate from is a list, not a view. */
  const goToComment = (c: CommentEntry) => {
    const root = (hostSheets.current?.[0] as any)?.parent;
    if (root?.getWorksheetActive?.() !== c.sheetIndex) root?.openWorksheet?.(c.sheetIndex);
    const ws = root?.worksheets?.[c.sheetIndex] ?? hostSheets.current?.[c.sheetIndex];
    const m = /^([A-Z]+)(\d+)$/.exec(c.cell);
    if (!ws || !m) return;
    const x = m[1].split("").reduce((a, ch) => a * 26 + (ch.charCodeAt(0) - 64), 0) - 1;
    const y = parseInt(m[2], 10) - 1;
    ws.updateSelectionFromCoords?.(x, y, x, y);
    (ws.records?.[y]?.[x]?.element as HTMLElement | undefined)
      ?.scrollIntoView?.({ block: "center" });
  };

  /** Commit the formula bar's text into the anchored cell. `setValueFromCoords(..., true)`
   *  writes the SOURCE, so "=SUM(A1:A2)" stays a live formula rather than literal text. */
  const commitFormulaBar = (next: string) => {
    if (!sel) return;
    const root = (hostSheets.current?.[0] as any)?.parent;
    const ws = root?.worksheets?.[root?.getWorksheetActive?.() ?? 0] ?? hostSheets.current?.[0];
    ws?.setValueFromCoords?.(sel.x, sel.y, next, true);
    setSel({ ...sel, value: next });
  };
  // Importing replaces the whole workbook, which a mount-once widget cannot do in place.
  // Bumping this re-runs the effect, which destroys and rebuilds the grid from the new
  // `initialValue` — the same path a fresh page load takes, so there is no second code
  // route that could drift from it.
  const [remount, setRemount] = useState(0);

  // jspreadsheet's fullscreen is a bare `classList.add("fullscreen")` on the host element —
  // no event, no callback — so watching the class is the only way to know we are in it.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const sync = () => setIsFullscreen(el.classList.contains("fullscreen"));
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => mo.disconnect();
  }, [remount]);

  /** Leave fullscreen by clicking jspreadsheet's own toolbar button rather than calling
   *  `fullscreen(false)` directly. The button both flips the state AND swaps its glyph
   *  between `fullscreen`/`fullscreen_exit`; the API call only does the former, leaving the
   *  toolbar showing "exit fullscreen" while no longer in it. */
  function exitFullscreen() {
    const btn = Array.from(host.current?.querySelectorAll(".jtoolbar-item") ?? [])
      .find((el) => el.querySelector("i")?.textContent === "fullscreen_exit");
    (btn as HTMLElement | undefined)?.click();
  }

  async function onImport(file: File | undefined) {
    if (!file) return;
    setImporting(true);
    setImportError("");
    try {
      const sheets = await importWorkbook(file);
      if (!sheets.length || sheets.every((s) => s.data.length === 0)) {
        throw new Error("that file has no readable rows");
      }
      const json = JSON.stringify({ version: 2, sheets });
      initialValue.current = json;
      onChangeRef.current(json);
      setRemount((n) => n + 1);
    } catch (e: any) {
      setImportError(e?.message ?? "could not read that file");
    } finally {
      setImporting(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    // The package uses TS's `export =` (CJS-style); Vite's dev/build interop puts the
    // callable on `.default` at RUNTIME, but that property doesn't exist on the type the
    // declarations describe — hence the narrow `as unknown as` bridging the two, isolated
    // to this one binding rather than casting anything else in the file.
    let jss: typeof import("jspreadsheet-ce") | null = null;
    const el = host.current;

    (async () => {
      const [mod] = await Promise.all([
        import("jspreadsheet-ce"),
        import("jspreadsheet-ce/dist/jspreadsheet.css"),
        import("jsuites/dist/jsuites.css"),
        // MANDATORY, not decorative. The stock toolbar emits Material Icons *ligatures*
        // (`<i class="material-icons">format_bold</i>`), and NEITHER jspreadsheet-ce nor
        // jsuites ships the font — jsuites.css only assumes it exists. Without this every
        // button rendered its raw ligature name ("format_align_left", "color_lens") as
        // visible text in an unwrappable flex row, which is what "all squeezed up" was.
        // Self-hosted rather than Google's CDN: no third-party request from a bank portal.
        // `filled.css` pulls ONE 126kB font; the package's other four variants would add
        // ~660kB for glyphs the toolbar never asks for.
        import("material-icons/iconfont/filled.css"),
      ]);
      if (cancelled || !el) return;
      const jspreadsheet = (mod as unknown as { default: typeof import("jspreadsheet-ce") }).default;
      jss = jspreadsheet;

      const stored = readWorkbook(initialValue.current);

      const worksheets = stored.map((s) => {
        // Seed cells with the FORMULA where one exists — jspreadsheet evaluates on load, so
        // the grid shows the computed value while the source stays editable. Without this a
        // reopened document would show numbers with no way to see or edit the formula.
        const data = s.data.map((row, r) =>
          row.map((cell, c) => s.formulas[`${colLetter(c)}${r + 1}`] ?? cell));
        const style: Record<string, string> = {};
        for (const [addr, props] of Object.entries(s.style)) {
          const css = styleToCss(props);
          if (css) style[addr] = css;
        }
        // `wordWrap` per column is what makes wrapping survive cell edits; `render` is what
        // makes a number format survive them, for the same reason — both are consulted by
        // jspreadsheet on every render rather than being applied once to the cells present.
        // `numberFormat` is our own key on the column config: the library passes the whole
        // config to `render`, so it rides along without needing a closure per column.
        const ncols = Math.max(s.colWidths.length, s.colWrap.length, s.colFormat.length);
        const columns = ncols
          ? Array.from({ length: ncols }, (_, i) => ({
              width: s.colWidths[i] ?? 100,
              wordWrap: !!s.colWrap[i],
              numberFormat: s.colFormat[i] ?? "",
              render: renderFormattedCell,
            }))
          : undefined;
        return {
          worksheetName: s.name,
          data: data.length ? data : undefined,
          style, columns,
          mergeCells: Object.fromEntries(Object.entries(s.merges).map(
            ([addr, m]) => [addr, [m.colspan, m.rowspan] as [number, number]])),
          // A spreadsheet should look like one: fill the space rather than opening as a
          // postage stamp, and keep filling it in FULLSCREEN. 30 rows is ~810px, which left
          // a measured 186px of blank white below the grid on a 1080p fullscreen; 60 rows
          // (~1.6k px) covers fullscreen to 1440p. Empty rows cost nothing in storage —
          // `emit` trims trailing blank rows/columns — and `minDimensions` is a floor, so a
          // trimmed document still reopens at this size.
          // 20 cols x 100px ≈ 2000px also fills a 1920 fullscreen horizontally; at 12 the
          // grid stopped ~700px short of the right edge, the same defect as the bottom gap.
          minDimensions: [20, 60] as [number, number],
          // Excel-style per-column filter dropdowns. Safe to enable: `getData` iterates
          // `options.data` directly and ignores the filtered `results` array, so filtering
          // the view can never cause us to persist only the visible rows (checked in the
          // library source — the opposite would be silent data loss).
          filters: true,
          // Explicit, though it is also the default. Until P6-S5b the context menu offered
          // "Add comments", wrote a native `title` tooltip, and `emit` never read it — so the
          // note was invisible unless you hovered the exact cell and was silently lost on the
          // next save. Owning the option is how that stops being an accident.
          allowComments: true,
          tableOverflow: true,
          tableHeight: "60vh",
          // REQUIRED for horizontal sizing at all. In jspreadsheet's source the overflow
          // handling is gated per axis: `tableHeight && (overflow-y:auto…)` and separately
          // `tableWidth && (overflow-x:auto, width=…)`. With tableWidth unset the entire
          // horizontal branch was skipped, so the grid both rendered at its intrinsic
          // ~650px AND could never scroll to columns past the edge.
          tableWidth: "100%",
          defaultColAlign: "left" as const,   // see cssToStyle — "left" means "unset"
        };
      });

      // Declared BEFORE the options object and assigned after, because jspreadsheet fires
      // change events DURING initialisation (seeding data and styles counts as a change).
      // A `const` assigned from the jspreadsheet() call would still be in the temporal dead
      // zone at that point, and the ReferenceError aborts the mount half-built — which
      // presents as "the toolbar silently disappeared", not as an obvious crash.
      // The null guard doubles as correct behaviour: emitting during mount would fire
      // onChange before the user has touched anything and mark the draft dirty immediately,
      // tripping DocumentDetail's unsaved-changes prompt on a document nobody edited.
      let sheets: import("jspreadsheet-ce").WorksheetInstance[] | null = null;

      /** The worksheet the user is currently looking at — tabs mean it isn't always [0]. */
      const currentSheet = () => {
        if (!sheets || !sheets.length) return null;
        const parent = (sheets[0] as any)?.parent;
        const live = (parent?.worksheets ?? sheets) as typeof sheets;
        const idx = parent?.getWorksheetActive?.() ?? 0;
        return live[idx] ?? live[0] ?? null;
      };

      const emit = () => {
        if (!sheets) return;
        // Read the LIVE worksheet list, not the array captured at mount: adding a sheet via
        // the tab bar's "+" creates a new instance that the original array never sees, so
        // serialising that array silently dropped every worksheet the user added after
        // opening the document.
        const parent = (sheets[0] as any)?.parent;
        const live = (parent?.worksheets ?? sheets) as typeof sheets;
        const book = live.map((inst, i): StoredSheet => {
          // getData(highlighted, processed). VERIFIED IN A BROWSER, because the bundled
          // type declarations describe this backwards — they say `processed: false` yields
          // the cells' innerHTML, which would be the computed value. It is the other way
          // round:
          //     getData(false, true)  -> "30"           the COMPUTED value
          //     getData(false, false) -> "=SUM(A1:A2)"  the formula SOURCE
          // Trusting the docstring here would have stored formula text in `data`, and every
          // exported PDF and Word file would have printed "=SUM(A1:A2)" instead of a number.
          const shown = inst.getData(false, true) as unknown as any[][];
          const source = inst.getData(false, false) as unknown as any[][];
          const styleMap = (inst.getStyle() as Record<string, string>) ?? {};

          const formulas: Record<string, string> = {};
          source.forEach((row, r) => row.forEach((cell, c) => {
            if (typeof cell === "string" && cell.startsWith("=")) {
              formulas[`${colLetter(c)}${r + 1}`] = cell;
            }
          }));

          const style: Record<string, CellStyle> = {};
          for (const [addr, css] of Object.entries(styleMap)) {
            const s = cssToStyle(css);
            if (s) style[addr] = s;
          }

          const merges: Record<string, { colspan: number; rowspan: number }> = {};
          const raw = (inst.getMerge?.() ?? {}) as Record<string, number[]>;
          for (const [addr, span] of Object.entries(raw)) {
            const [colspan = 1, rowspan = 1] = span ?? [];
            if (colspan > 1 || rowspan > 1) merges[addr] = { colspan, rowspan };
          }

          const widths = inst.getWidth() as unknown;
          // Read formats off the LIVE column options — the toolbar picker mutates them.
          const colOpts = ((inst as any).options?.columns ?? []) as any[];
          const fmts = colOpts.map((col) => String(col?.numberFormat ?? ""));

          // A formatted column cannot be read out of `shown`: that is `element.innerHTML`,
          // which now carries OUR rendering ("₹1,234.57"), so storing it would put display
          // text in `data` and lose the number. Unformatted columns keep the original path
          // byte for byte, so nothing that predates P6-S4 can drift.
          const data = trimBlankEdges(
            shown.map((row, r) => row.map((c, x) => {
              const text = c == null ? "" : String(c);
              const fmt = fmts[x] || "";
              if (!fmt) return text;
              const src = source[r]?.[x];
              if (typeof src !== "string" || !src.startsWith("=")) {
                // Not a formula: `options.data` already holds the raw value the user typed,
                // at full precision, and text (a column header) passes through untouched.
                return src == null ? "" : String(src);
              }
              // A formula's computed value exists ONLY in the DOM, so take the raw value
              // `renderFormattedCell` stashed before it overwrote the cell. `unformatCell`
              // is the fallback for a cell that was never re-rendered; it is lossy past two
              // decimals, which is exactly why the stash is preferred.
              const stash = (inst as any).records?.[r]?.[x]?.element?.dataset?.raw;
              return stash !== undefined ? String(stash) : unformatCell(text, fmt);
            })),
            formulas, style, merges);
          return {
            // Read the name off the live instance — a sheet added or renamed at runtime
            // isn't in `stored`, which is only the state the document opened with.
            name: String((inst as any).options?.worksheetName
                         ?? stored[i]?.name ?? `Sheet${i + 1}`),
            data,
            formulas, style, merges,
            colWidths: Array.isArray(widths)
              ? widths.map((w) => Number(w)).filter((w) => Number.isFinite(w) && w > 0) : [],
            // Read wrap off the live column options — the toolbar button mutates them.
            colWrap: dropTrailingEmpty(colOpts.map((col) => !!col?.wordWrap), false),
            colFormat: dropTrailingEmpty(fmts, ""),
            // `getComments()` scans the cells' `title` attributes, which is where
            // `setComments` puts them — so this reads what is actually on screen rather than
            // a parallel bookkeeping object that could drift.
            comments: (inst.getComments?.() ?? {}) as Record<string, string>,
          };
        });
        onChangeRef.current(JSON.stringify({ version: 2, sheets: book }));
        setComments(collectComments(sheets));
      };

      /** Which columns a column-level toolbar control acts on: the selected column headers
       *  if any, otherwise the column the anchored cell sits in. */
      const targetColumns = (inst: any): number[] => {
        const sel: number[] = inst?.getSelectedColumns?.() ?? [];
        return sel.length ? sel : [(inst?.getSelection?.() ?? [0])[0] ?? 0];
      };

      const WRAP_ITEM = {
        content: "wrap_text",
        tooltip: "Wrap text in this column",
        onclick: (_el: any, _tb: any, itemEl: any) => {
          const inst: any = currentSheet();
          if (!inst) return;
          const cols = targetColumns(inst);
          const opts = inst.options;
          opts.columns = opts.columns ?? [];
          // Resolve a mixed selection to ONE outcome from the first column's current state,
          // rather than flipping each column independently.
          const turnOn = !opts.columns[cols[0]]?.wordWrap;
          for (const c of cols) {
            opts.columns[c] = { ...(opts.columns[c] ?? {}), wordWrap: turnOn };
            // The option governs FUTURE renders; existing cells need the inline style now,
            // or nothing visibly changes until each cell is retyped.
            for (const row of inst.records ?? []) {
              if (row?.[c]?.element) row[c].element.style.whiteSpace = turnOn ? "pre-wrap" : "";
            }
          }
          itemEl?.classList?.toggle("jtoolbar-active", turnOn);
          emit();
        },
        updateState: (_t: any, _ti: any, itemEl: any, ws: any) => {
          const c = targetColumns(ws)[0];
          itemEl?.classList?.toggle("jtoolbar-active", !!ws?.options?.columns?.[c]?.wordWrap);
        },
      };

      /**
       * P6-S4: the column number-format picker — currency (per column, so one workbook can
       * hold a rupee column beside a dollar one) and percent.
       *
       * A `type: "select"` item with NO `content`: jSuites' picker shows a fixed icon when
       * `content` is set and the CURRENT option's label when it isn't, and a control that
       * cannot show which format a column already has would be a control that lies. The stock
       * font-family picker is built the same way, for the same reason.
       */
      const FORMAT_ITEM = {
        type: "select",
        width: "112px",
        // `id` rather than `tooltip`: jSuites sets it as an attribute before it branches on
        // item type, so it is the one handle that survives on a select. See FORMAT_ITEM_ID.
        id: FORMAT_ITEM_ID,
        options: FORMAT_OPTIONS.map((f) => f.label),
        render: (label: string) => `<span class="jss-format-option">${label}</span>`,
        // (itemEl, picker, value, value2, valueIndex) — index into our own list rather than
        // matching on the label, which is display copy and free to change.
        onchange: (_itemEl: any, _picker: any, _v: any, _v2: any, index: any) => {
          const inst: any = currentSheet();
          if (!inst) return;
          const fmt = FORMAT_OPTIONS[Number(index)]?.value ?? "";
          const opts = inst.options;
          opts.columns = opts.columns ?? [];
          for (const c of targetColumns(inst)) {
            // `render` governs FUTURE renders — including every cell edited from now on, and
            // every row inserted — which is what makes the format survive editing. Existing
            // cells need re-formatting now, or nothing visibly changes until each is retyped.
            opts.columns[c] = { ...(opts.columns[c] ?? {}), numberFormat: fmt,
                                render: renderFormattedCell };
            for (const row of inst.records ?? []) {
              if (row?.[c]?.element) reformatInPlace(row[c].element, fmt);
            }
          }
          emit();
        },
        updateState: (_t: any, _ti: any, itemEl: any, ws: any) => {
          // The only guaranteed handle on this element: jspreadsheet builds its toolbar
          // asynchronously, so querying for it straight after the constructor returns finds
          // nothing. See FORMAT_ITEM_ID for why it has no accessible name of its own.
          itemEl?.setAttribute?.("aria-label", FORMAT_ITEM_LABEL);
          const fmt = String(ws?.options?.columns?.[targetColumns(ws)[0]]?.numberFormat ?? "");
          const idx = Math.max(0, FORMAT_OPTIONS.findIndex((f) => f.value === fmt));
          // One argument, not two: jSuites' `setValue(k, e)` only fires `onchange` when `e`
          // is passed, and firing it here would re-apply the format on every selection move.
          itemEl?.picker?.setValue?.(idx);
        },
      };

      // NOTE: `sheets` is assigned AFTER this call returns, but jspreadsheet fires events
      // during mount — hence the null guards on it elsewhere. `hostSheets` is the ref the
      // formula bar reads, populated on the line after the constructor.
      sheets = jspreadsheet(el, {
        // Append "Wrap text" and the number-format picker to the STOCK toolbar rather than
        // replacing it — the default set (undo/redo, font, size, align, bold, colours, merge,
        // borders, fullscreen) has neither. `wrap_text` is a Material Icons ligature, which
        // renders because we self-host that font.
        //
        // NB the declared signature is `(items: ToolbarItem[]) => ToolbarItem[]`, but at
        // RUNTIME jspreadsheet passes `{items: ToolbarItem[]}` and expects that shape back
        // (`t={items:…}; t=config.toolbar(t)`). Handle both rather than trust the .d.ts —
        // same class of mismatch as getData's inverted `processed` flag.
        toolbar: ((arg: any) => {
          const items = Array.isArray(arg) ? arg : (arg?.items ?? []);
          const next = [...items, WRAP_ITEM, { type: "divisor" }, FORMAT_ITEM];
          return Array.isArray(arg) ? next : { ...arg, items: next };
        }) as any,
        tabs: true,         // worksheet tabs + the "add sheet" button
        // P6: the formula bar mirrors and edits the selected cell, so it needs to know what
        // is selected. jspreadsheet reports the range; the bar addresses the anchor (x1,y1).
        onselection: (_el: any, x1: number, y1: number) => {
          const root = (hostSheets.current?.[0] as any)?.parent;
          const ws = root?.worksheets?.[root?.getWorksheetActive?.() ?? 0]
                     ?? hostSheets.current?.[0];
          // `getValueFromCoords(x, y, processed)` — processed TRUE returns the computed
          // value ("20"), FALSE returns the source ("=A1*5"). The formula bar wants the
          // source; the grid already shows the value. Same inversion trap as `getData`,
          // whose bundled types document it backwards (see the note at the top of this file).
          setSel({ x: x1, y: y1, value: String(ws?.getValueFromCoords?.(x1, y1, false) ?? "") });
        },
        onchange: emit, onafterchanges: emit, onchangestyle: emit,
        oncomments: emit,
        onmerge: emit, onresizecolumn: emit,
        oninsertrow: emit, ondeleterow: emit,
        oninsertcolumn: emit, ondeletecolumn: emit,
        // Adding or removing a worksheet is a content change like any other. Without these
        // two, a sheet added via the tab bar appeared in the UI and then vanished on save.
        oncreateworksheet: emit, ondeleteworksheet: emit,
        worksheets,
      });
      hostSheets.current = sheets;

      // Stored comments must be REPLAYED through `setComments`, not seeded via the worksheet
      // option. `comments` in the options object is only ever written by `setComments` itself;
      // nothing reads it at first render, so a seeded workbook would carry the notes in memory
      // while showing no marker and no tooltip. Read out of the library source, not guessed —
      // the same class of trap as `wordWrap` and the number-format mask.
      stored.forEach((sheet, i) => {
        const entries = Object.entries(sheet.comments ?? {});
        if (entries.length && sheets?.[i]) {
          (sheets[i] as any).setComments?.(Object.fromEntries(entries));
        }
      });
      // …and that replay counts as a change, so snapshot AFTER it rather than letting the
      // first real edit look like it added every comment in the document.
      setComments(collectComments(sheets));
    })();

    return () => {
      cancelled = true;
      if (el && jss) jss.destroy(el as any);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remount]);

  return (
    // `sheet-editor` is the scoping hook for the .jss_* rules in index.css.
    // NOTE: no `overflow-hidden` here. It used to be the only clipper on the whole ancestor
    // chain, and combined with the missing tableWidth a wide grid was cut off with no
    // scrollbar to reach the rest. jspreadsheet now owns its own overflow.
    // P6: full-bleed. The card border and radius are gone — the grid IS the view, edge to
    // edge, the way a spreadsheet app looks. This also removed the reason the separate
    // fullscreen mode existed (the grid used to be boxed inside a narrow card).
    <div className="sheet-editor bg-paper">
      {/* P6 formula bar: the cell reference, an fx marker, and the cell's SOURCE. It shows
          "=SUM(A1:A2)" where the grid shows 30 — a formula bar that mirrored the computed
          value would be a second copy of the cell rather than a way to see behind it. */}
      <div className="flex items-center gap-2 border-b border-bd bg-paper px-3 py-1.5">
        <span className="min-w-[52px] rounded-md border border-bd bg-paper px-2 py-0.5 text-center
                         font-mono text-caption font-semibold">
          {sel ? `${colLetter(sel.x)}${sel.y + 1}` : "—"}
        </span>
        <span className="font-mono text-caption italic text-txt3">fx</span>
        <input
          aria-label="Formula bar"
          value={sel?.value ?? ""}
          disabled={!sel}
          onChange={(e) => setSel(sel ? { ...sel, value: e.target.value } : null)}
          onBlur={(e) => commitFormulaBar(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { commitFormulaBar((e.target as HTMLInputElement).value);
                                     (e.target as HTMLInputElement).blur(); }
          }}
          className="min-w-0 flex-1 bg-transparent font-mono text-label outline-none
                     disabled:cursor-not-allowed" />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-bd bg-canvas px-3 py-1.5">
        {/* P6-S5b: comments existed in the context menu long before there was any way to READ
            them — they were a native tooltip on one cell, findable only by hovering the exact
            cell that had one. This is the view. */}
        <button type="button" onClick={() => setShowComments((v) => !v)}
          aria-expanded={showComments}
          className="btn py-1 text-label">
          Comments{comments.length ? ` (${comments.length})` : ""}
        </button>
        <label className="btn cursor-pointer py-1 text-label">
          {importing ? "Reading…" : "Import .xlsx / .csv"}
          <input type="file" accept=".xlsx,.xls,.csv" className="hidden" disabled={importing}
            onChange={(e) => {
              const f = e.target.files?.[0];
              // Snapshot before the reset — `files` is a live FileList and clearing the
              // input empties it. Same trap as the P5-S1 evidence-upload race.
              e.target.value = "";
              void onImport(f);
            }} />
        </label>
        <span className="text-caption text-txt3">
          Replaces every sheet in this draft. Values and formulas are imported, and the
          import saves itself like any other edit.
        </span>
      </div>
      {importError && (
        <div role="alert" className="border-b border-bd bg-bad-bg px-3 py-1.5 text-label text-bad">
          Could not import — {importError}.
        </div>
      )}
      {showComments && (
        <div role="region" aria-label="Cell comments"
          className="max-h-56 overflow-auto border-b border-bd bg-paper px-3 py-2">
          {comments.length === 0 ? (
            <p className="text-caption text-txt3">
              No comments yet. Right-click a cell and choose “Add comments” to leave a note for
              whoever reviews this.
            </p>
          ) : (
            <ul className="space-y-1">
              {comments.map((c) => (
                <li key={`${c.sheetIndex}:${c.cell}`}>
                  <button type="button" onClick={() => goToComment(c)}
                    className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left
                               hover:bg-canvas">
                    <span className="shrink-0 font-mono text-caption font-semibold text-txt2">
                      {comments.some((o) => o.sheetIndex !== c.sheetIndex)
                        ? `${c.sheet}!${c.cell}` : c.cell}
                    </span>
                    <span className="text-label text-txt">{c.note}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div ref={host} />

      {/* In fullscreen the sheet covers the whole viewport — including DocumentDetail's own
          "Save draft" and "Done". Without this bar the only way to save was to leave
          fullscreen first, which is easy to miss and risks losing work. Rendered as a
          sibling at a HIGHER z-index than the fullscreen box itself (60, set in index.css)
          rather than inside it, so jspreadsheet never manages or overwrites these nodes. */}
      {isFullscreen && (
        // role/aria-label are not decoration: DocumentDetail already renders a "Save draft"
        // button, so without a named group there would be two identically-labelled buttons
        // in the accessibility tree — ambiguous for a screen reader, and for any test
        // reaching for one of them.
        <div role="toolbar" aria-label="Fullscreen sheet actions"
          className="fixed right-4 top-3 z-[61] flex items-center gap-2 rounded-lg
                     border border-bd bg-paper px-2 py-1.5 shadow-drawer">
          {/* P6: the Save button that used to live here is gone with autosave. Keeping it
              would have been worse than removing it — the props stopped being passed, so it
              would have rendered a primary button that did nothing. Exit remains, because
              jspreadsheet's own exit control sits under our app header. */}
          <button type="button" onClick={exitFullscreen} className="btn py-1 text-label">
            Exit fullscreen
          </button>
        </div>
      )}
    </div>
  );
}

/** 0 -> A, 25 -> Z, 26 -> AA. Mirrors `_col_letter` in api/render.py and `colLetter` in
 *  DocBody.tsx — the three must agree, since they address the same stored cells. */
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
