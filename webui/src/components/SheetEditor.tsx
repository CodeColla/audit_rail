import { useEffect, useRef, useState } from "react";

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
};

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

/** `[true,false,false]` -> `[true]`. Positional, so dropping only the TAIL cannot shift
 *  any column's meaning. */
function dropTrailingFalse(flags: boolean[]): boolean[] {
  let last = -1;
  flags.forEach((f, i) => { if (f) last = i; });
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
    ({ name, data: [], formulas: {}, style: {}, merges: {}, colWidths: [], colWrap: [] });

  if (parsed.version === 2 && Array.isArray(parsed.sheets) && parsed.sheets.length) {
    return parsed.sheets.map((s: any, i: number) => ({
      ...blank(String(s?.name ?? `Sheet${i + 1}`)),
      data: Array.isArray(s?.data) ? s.data : [],
      formulas: s?.formulas && typeof s.formulas === "object" ? s.formulas : {},
      style: s?.style && typeof s.style === "object" ? s.style : {},
      merges: s?.merges && typeof s.merges === "object" ? s.merges : {},
      colWidths: Array.isArray(s?.colWidths) ? s.colWidths : [],
      colWrap: Array.isArray(s?.colWrap) ? s.colWrap : [],
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
    };
    const ref = ws["!ref"];
    if (!ref) return sheet;
    const range = XLSX.utils.decode_range(ref);
    for (let r = range.s.r; r <= range.e.r; r++) {
      const row: string[] = [];
      for (let c = range.s.c; c <= range.e.c; c++) {
        const cell = ws[XLSX.utils.encode_cell({ r, c })] as any;
        // `w` is the formatted text Excel displays; fall back to the raw value.
        row.push(cell == null ? "" : String(cell.w ?? cell.v ?? ""));
        if (cell?.f) sheet.formulas[`${colLetter(c - range.s.c)}${r - range.s.r + 1}`] = `=${cell.f}`;
      }
      sheet.data.push(row);
    }
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
        // `wordWrap` per column is what makes wrapping survive cell edits.
        const ncols = Math.max(s.colWidths.length, s.colWrap.length);
        const columns = ncols
          ? Array.from({ length: ncols }, (_, i) => ({
              width: s.colWidths[i] ?? 100,
              wordWrap: !!s.colWrap[i],
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
          const data = trimBlankEdges(
            shown.map((row) => row.map((c) => (c == null ? "" : String(c)))),
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
            // Trailing `false`s are dropped for the same reason blank rows are: an
            // untouched 20-column grid would otherwise store 20 booleans of nothing in
            // every document and every version diff.
            colWrap: dropTrailingFalse(
              (((inst as any).options?.columns ?? []) as any[]).map((col) => !!col?.wordWrap)),
          };
        });
        onChangeRef.current(JSON.stringify({ version: 2, sheets: book }));
      };

      const WRAP_ITEM = {
        content: "wrap_text",
        tooltip: "Wrap text in this column",
        onclick: (_el: any, _tb: any, itemEl: any) => {
          const inst: any = currentSheet();
          if (!inst) return;
          const sel: number[] = inst.getSelectedColumns?.() ?? [];
          const cols = sel.length ? sel : [(inst.getSelection?.() ?? [0])[0] ?? 0];
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
          const sel: number[] = ws?.getSelectedColumns?.() ?? [];
          const c = sel.length ? sel[0] : (ws?.getSelection?.() ?? [0])[0] ?? 0;
          itemEl?.classList?.toggle("jtoolbar-active", !!ws?.options?.columns?.[c]?.wordWrap);
        },
      };

      sheets = jspreadsheet(el, {
        // Append "Wrap text" to the STOCK toolbar rather than replacing it — the default set
        // (undo/redo, font, size, align, bold, colours, merge, borders, fullscreen) has no
        // wrap control of its own. `wrap_text` is a Material Icons ligature, which renders
        // because we self-host that font.
        //
        // NB the declared signature is `(items: ToolbarItem[]) => ToolbarItem[]`, but at
        // RUNTIME jspreadsheet passes `{items: ToolbarItem[]}` and expects that shape back
        // (`t={items:…}; t=config.toolbar(t)`). Handle both rather than trust the .d.ts —
        // same class of mismatch as getData's inverted `processed` flag.
        toolbar: ((arg: any) => {
          const items = Array.isArray(arg) ? arg : (arg?.items ?? []);
          const next = [...items, WRAP_ITEM];
          return Array.isArray(arg) ? next : { ...arg, items: next };
        }) as any,
        tabs: true,         // worksheet tabs + the "add sheet" button
        onchange: emit, onafterchanges: emit, onchangestyle: emit,
        onmerge: emit, onresizecolumn: emit,
        oninsertrow: emit, ondeleterow: emit,
        oninsertcolumn: emit, ondeletecolumn: emit,
        // Adding or removing a worksheet is a content change like any other. Without these
        // two, a sheet added via the tab bar appeared in the UI and then vanished on save.
        oncreateworksheet: emit, ondeleteworksheet: emit,
        worksheets,
      });
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
    <div className="sheet-editor rounded-xl border border-bd bg-paper">
      <div className="flex flex-wrap items-center gap-2 border-b border-bd bg-canvas px-3 py-1.5">
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
          Replaces every sheet in this draft. Values and formulas are imported; import is not
          saved until you click <b>Save draft</b>.
        </span>
      </div>
      {importError && (
        <div role="alert" className="border-b border-bd bg-bad-bg px-3 py-1.5 text-label text-bad">
          Could not import — {importError}.
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
