/**
 * Column number formats for SHEET documents (P6-S4) — currency and percent.
 *
 * Shared by `SheetEditor.tsx` (the grid) and `DocBody.tsx` (the read view) so the two cannot
 * drift, and mirrored in Python by `format_cell_value` in `api/render.py`, which is what the
 * PDF and DOCX exports go through. Three implementations of one rule is one too many, but the
 * renderer is Python and the editor is a browser widget — so the rule is written down here,
 * kept deliberately tiny, and covered on both sides.
 *
 * **The format is per COLUMN, not per cell.** That is forced by the widget: jspreadsheet
 * reads formatting off `options.columns[i]` — its `render` hook receives the column config
 * and there is no per-cell equivalent — so a per-cell format would be a setting the editor
 * could never honour. The same constraint already made `colWrap` per column.
 *
 * **Formatting is presentation; the stored value stays raw.** `data` holds "1234.5" and the
 * column holds "currency:INR", so the cell can be re-formatted, or unformatted, without the
 * number ever having been lost — and the .xlsx export can write a real number with a real
 * Excel number format instead of unsortable text.
 */

/** The closed set of currencies a column can carry. Mirrors `render.CURRENCY_SYMBOLS`; the
 *  server rejects anything else, so adding one means adding it in both places. */
export const CURRENCY_SYMBOLS: Record<string, string> = {
  INR: "₹", USD: "$", EUR: "€", GBP: "£",
};

/**
 * What counts as a number for formatting purposes.
 *
 * Deliberately NOT `Number(text)`: that accepts "", "  12  ", "0x1f" and "Infinity", while
 * Python's `float()` additionally accepts "1_000" and "nan". Either would format cells the
 * other side left alone, and the editor and the PDF would disagree. This is the shared
 * definition — `_NUMERIC_RE` in `api/render.py` is its twin.
 */
export const NUMERIC_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

/** What the toolbar picker offers, in order. Index 0 is "no format", which is also what
 *  clearing a column stores — an empty string, not a missing entry, because the list is
 *  positional. */
export const FORMAT_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Plain" },
  { value: "currency:INR", label: "₹ Rupee" },
  { value: "currency:USD", label: "$ Dollar" },
  { value: "currency:EUR", label: "€ Euro" },
  { value: "currency:GBP", label: "£ Pound" },
  { value: "percent", label: "% Percent" },
];

/**
 * Apply a column format to one cell's stored value.
 *
 * **Text falls through unchanged.** A register's header sits in the same column as its
 * figures, so "Amount" under a currency format must stay "Amount" rather than rendering as a
 * bare "₹" — which is exactly what jspreadsheet's own `mask` option does, and the reason this
 * is done through the `render` hook instead.
 *
 * **Percent reads the stored number as a RATIO**, Excel's convention: 0.15 shows as 15.00%.
 * Not a free choice — the exported .xlsx uses Excel's `0.00%`, which multiplies by 100
 * regardless, so any other reading would make the workbook disagree with the PDF.
 */
export function formatCell(text: string, fmt: string): string {
  if (!fmt || !NUMERIC_RE.test(text ?? "")) return text;
  const value = Number(text);
  const fixed = (n: number) =>
    n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (fmt === "percent") return `${fixed(value * 100)}%`;
  const symbol = CURRENCY_SYMBOLS[fmt.split(":")[1]] ?? "";
  // Sign OUTSIDE the symbol ("-₹5.00", not "₹-5.00") — the accounting convention, and the
  // same spelling `format_cell_value` produces in api/render.py.
  return value < 0 ? `-${symbol}${fixed(Math.abs(value))}` : `${symbol}${fixed(value)}`;
}

/**
 * The inverse of `formatCell`, for recovering a value from text we ourselves rendered.
 *
 * Used in exactly one place — `SheetEditor`'s save path, for a FORMULA cell whose computed
 * result is only readable out of the rendered DOM — and only as a fallback when the raw value
 * stashed on the cell is missing. It is lossy by construction (the display carries two
 * decimals, so "₹1,234.57" cannot yield back 1234.5678), which is precisely why the stash is
 * the primary path and this is the safety net.
 */
export function unformatCell(text: string, fmt: string): string {
  if (!fmt) return text;
  const stripped = (text ?? "").replace(/[^\d.eE+-]/g, "");
  if (!NUMERIC_RE.test(stripped)) return text;
  const n = Number(stripped);
  return fmt === "percent" ? String(n / 100) : String(n);
}
