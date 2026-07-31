import { useEffect, useRef } from "react";

/**
 * The spreadsheet-document authoring surface (P5-S2), parallel to `RichTextEditor` — same
 * `{value, onChange}` contract, so `DocumentDetail.tsx` can dispatch between the two purely
 * on `content_format` without either editor knowing the other exists.
 *
 * `value`/`onChange` carry a JSON STRING, not markup: `{"data": [[cell,...],...],
 * "bold": ["A1",...], "align": {"A1":"center",...}}` — the same shape `api/render.py`'s
 * `parse_sheet` validates server-side. See that module's docstring for why the format is
 * this narrow: values plus bold/alignment only, no formulas, no arbitrary per-cell CSS.
 *
 * jspreadsheet-ce is loaded via dynamic `import()`, matching how `docx-preview`/`xlsx`
 * are already handled in `FilePreview.tsx`, so the ~450kB library never reaches a user who
 * doesn't open a spreadsheet document.
 *
 * Mounts the widget ONCE and treats it as effectively uncontrolled after that — `value`
 * only ever changes here as an ECHO of this component's own `onChange` (the parent just
 * stores whatever was last reported), so re-syncing external `value` changes back into a
 * live jspreadsheet-ce instance would be pointless work that could also disrupt whatever
 * cell the user has selected mid-edit. `Editor` in DocumentDetail.tsx already gates
 * mounting this on the version's data being loaded, so there is no "content arrives after
 * mount" case to handle either, unlike `RichTextEditor`.
 */
export function SheetEditor({ value, onChange }: {
  value: string;
  onChange: (json: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const initialValue = useRef(value);   // read once; see the mount-once note above

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
      ]);
      if (cancelled || !el) return;
      const jspreadsheet = (mod as unknown as { default: typeof import("jspreadsheet-ce") }).default;
      jss = jspreadsheet;

      let parsed: { data?: unknown; bold?: unknown; align?: unknown } = {};
      try { parsed = JSON.parse(initialValue.current || "{}"); } catch { /* fresh sheet */ }
      const data = Array.isArray(parsed.data) && parsed.data.length
        ? (parsed.data as string[][])
        : Array.from({ length: 8 }, () => Array(5).fill(""));
      const bold = new Set(Array.isArray(parsed.bold) ? (parsed.bold as string[]) : []);
      const align = (parsed.align && typeof parsed.align === "object" ? parsed.align : {}) as Record<string, string>;

      // Seed jspreadsheet-ce's own style map from our two supported properties — its
      // native format is exactly `{address: "css;declarations"}`, so this is a direct fit.
      const style: Record<string, string> = {};
      for (const addr of bold) style[addr] = "font-weight:bold;";
      for (const [addr, a] of Object.entries(align)) {
        style[addr] = (style[addr] ?? "") + `text-align:${a};`;
      }

      // `onchange`/`onafterchanges`/`onchangestyle` are top-level SpreadsheetOptions
      // callbacks (they receive the WorksheetInstance as their first argument), not
      // per-worksheet options — same misplacement pattern as `toolbar` above.
      const emit = (inst: import("jspreadsheet-ce").WorksheetInstance | undefined) => {
        if (!inst) return;
        const rows = inst.getData() as unknown as (string | number | boolean | null)[][];
        // getStyle() with no args returns a computed entry for EVERY cell, not just ones
        // touched — it always includes `text-align`, because jspreadsheet-ce renders one by
        // default (defaultColAlign below). Confirmed empirically: on a freshly mounted,
        // completely untouched grid, getStyle() already reports "text-align: center;" for
        // every cell. Recording all of those would both bloat the stored JSON and silently
        // align every cell the API/PDF/DOCX path had no reason to touch. `defaultColAlign:
        // "left"` below makes that baseline match how an ABSENT `align` entry already renders
        // everywhere else (render.py's sheet_json_to_html, DocBody's SheetTable — no style at
        // all), so filtering out "left" here is filtering out "unset", not losing information.
        const allStyle = inst.getStyle() as Record<string, string>;
        const outBold: string[] = [];
        const outAlign: Record<string, string> = {};
        for (const [addr, css] of Object.entries(allStyle)) {
          if (/font-weight\s*:\s*bold/i.test(css)) outBold.push(addr);
          const m = /text-align\s*:\s*(left|center|right)/i.exec(css);
          if (m && m[1].toLowerCase() !== "left") outAlign[addr] = m[1].toLowerCase();
        }
        onChangeRef.current(JSON.stringify({
          data: rows.map((row) => row.map((c) => (c == null ? "" : String(c)))),
          bold: outBold, align: outAlign,
        }));
      };

      jspreadsheet(el, {
        toolbar: true,   // top-level option, not per-worksheet — the default toolbar
        onchange: emit, onafterchanges: emit, onchangestyle: emit,
        worksheets: [{
          data, style,
          minDimensions: [6, 10],
          tableOverflow: true, tableHeight: "60vh",
          defaultColAlign: "left",   // matches the read pipeline's own default — see emit()
        }],
      });
    })();

    return () => {
      cancelled = true;
      if (el && jss) jss.destroy(el as any);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="overflow-hidden rounded-xl border border-bd bg-paper">
      <p className="border-b border-bd bg-canvas px-3 py-1.5 text-[11.5px] text-txt3">
        The toolbar offers more, but only <strong>bold</strong> and <strong>alignment</strong> are
        saved with the document — other formatting won't survive a reload.
      </p>
      <div ref={host} />
    </div>
  );
}
