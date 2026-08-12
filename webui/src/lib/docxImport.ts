import { uploadDocImage } from "../components/DocImage";

/**
 * Bring an existing Word policy into a draft (P6-S5).
 *
 * The spreadsheet editor has had "Import .xlsx / .csv" since P5-S2; prose documents had
 * nothing, so every legacy policy had to be retyped. This is the equivalent, and it runs in
 * the browser for the same reason SheetJS does in `SheetEditor.importWorkbook` — the file
 * never needs to reach the server as a file, only its converted content does.
 *
 * **`mammoth`, not `docx-preview`** — even though `docx-preview` is already a dependency and
 * would have been free. That library is a *renderer*: it emits `<div>`/`<span>` soup carrying
 * inline styles to reproduce Word's visual layout. Our sanitiser allows neither tag and admits
 * seven style properties, so its output would arrive as an unstructured wall of text with
 * every heading, list and indent gone. Mammoth is a *semantic* converter — it maps Word's
 * paragraph styles onto `h1`-`h6`, `p`, `ul`/`ol`/`li`, `strong`/`em`, `table`, `a` — which is
 * very nearly our allow-list verbatim. Right tool, different job.
 *
 * Dynamically imported so a user who never imports never downloads it, matching how `xlsx`
 * and `docx-preview` are already handled.
 */

/** Word constructs mammoth drops, plus the ones OUR sanitiser drops afterwards. Stated up
 *  front in the UI rather than discovered after the document has been approved. */
export const IMPORT_LOSES = [
  "page headers, footers and page breaks",
  "text boxes, shapes, charts and SmartArt",
  "comments and tracked changes (the final text is kept)",
  "footnote and endnote markers",
  "fonts, colours and sizes that are not part of a Word style",
];

/** Refused past this many pictures. Each is a separate upload, and a 200-image Word file
 *  would otherwise sit there firing requests with no way to tell whether it had hung. */
export const MAX_IMPORT_IMAGES = 50;

export type ImportResult = {
  html: string;
  /** Mammoth's own per-file conversion notes — "Unrecognised paragraph style: 'Caption'" and
   *  the like. Surfaced verbatim: a real report about THIS file beats a generic blurb. */
  messages: string[];
  imagesImported: number;
  imagesFailed: number;
};

/**
 * Convert a .docx to HTML, uploading every embedded picture through the document image store.
 *
 * The `convertImage` hook is not optional decoration. Mammoth's default is
 * `mammoth.images.dataUri`, which emits `<img src="data:image/png;base64,…">` — and our
 * sanitiser strips that src on the very first save, so every picture in the imported policy
 * would vanish silently some seconds after the author saw it arrive. Routing each image
 * through `POST /documents/{id}/images` gives them the same sniffing, the same size cap and
 * the same tenant scoping as one inserted by hand.
 */
export async function importDocx(docId: string, file: File): Promise<ImportResult> {
  // The browser build, not the Node entry point: the default export pulls in fs/zlib and
  // fails to bundle. No published types for the browser path, hence the cast.
  const mammoth: any = await import(
    /* @vite-ignore */ "mammoth/mammoth.browser" as string);

  let imagesImported = 0;
  let imagesFailed = 0;
  const extra: string[] = [];

  const convertImage = mammoth.images.imgElement(async (image: any) => {
    if (imagesImported + imagesFailed >= MAX_IMPORT_IMAGES) {
      imagesFailed += 1;
      return {};                       // no src -> the sanitiser drops the element entirely
    }
    try {
      const buffer = await image.read();
      const blob = new Blob([buffer], { type: image.contentType || "image/png" });
      const { fileId } = await uploadDocImage(
        docId, new File([blob], "imported", { type: blob.type }));
      imagesImported += 1;
      return { src: `/api/documents/images/${fileId}` };
    } catch {
      // Word embeds EMF/WMF vectors for anything pasted from Visio or SmartArt, and the
      // server refuses those. One unusable picture must not fail the whole import.
      imagesFailed += 1;
      return {};
    }
  });

  const result = await mammoth.convertToHtml(
    { arrayBuffer: await file.arrayBuffer() }, { convertImage });

  if (imagesFailed) {
    extra.push(imagesFailed === 1
      ? "1 image could not be imported (Word vector drawings are not supported)."
      : `${imagesFailed} images could not be imported (Word vector drawings are not supported).`);
  }
  return {
    // `<sup>`/`<sub>` are NOT on the sanitiser's allow-list, and should not be added: the
    // rule there is that the list is a superset of what the EDITOR can emit, and TipTap emits
    // neither. Flattening them here means what the author sees immediately after import is
    // exactly what will still be there after the first save — a smaller, earlier loss than
    // watching footnote markers disappear later.
    html: stripUnsupported(result.value ?? ""),
    messages: [...new Set<string>(
      (result.messages ?? []).map((m: any) => String(m.message)))].concat(extra),
    imagesImported,
    imagesFailed,
  };
}

function stripUnsupported(html: string): string {
  return html.replace(/<\/?(sup|sub)>/gi, "");
}
