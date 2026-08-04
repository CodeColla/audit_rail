import { useEffect, useState } from "react";
import { Modal } from "../lib/ui";
import { fetchBlob } from "../lib/api";
import { FilePreview } from "./FilePreview";

/**
 * The clickable, previewable presentation of a stored artifact — generalised from
 * `Workspace.tsx`'s `EvidencePreviewButton` (the one place in the app that already did this
 * well) so every list of attachments can share it instead of re-inventing it.
 *
 * P4/P5 audit found three distinct failure levels across the app: an unstyled plain `<span>`
 * with no link and no preview at all (`Registers.tsx`'s `AttachEvidenceCard`, the worst
 * case), a button with no underline that just downloads instead of previewing
 * (`AuditorApp.tsx`), and a button with no underline and no thumbnail (the Evidence vault
 * list itself). This component is the one answer to all three.
 *
 * `mimeType` is optional: most call sites in the app only have `evidence_type` (a category
 * like "certificate", not a MIME type) available, and a real thumbnail needs to fetch the
 * actual bytes — worth doing only where the caller already knows this is an image. Callers
 * without it still get the underline, a type-appropriate icon, and click-to-preview, which
 * is already a large step up from every existing call site.
 */
export function AttachmentLink({ id, title, mimeType, fileUrl }: {
  id: string; title: string; mimeType?: string | null;
  /** Defaults to the evidence file route; register-scoped attachments (agreements, asset
   * photos) pass their own. */
  fileUrl?: string;
}) {
  const [open, setOpen] = useState(false);
  const url = fileUrl ?? `/evidence/${id}/file`;
  const isImage = (mimeType ?? "").startsWith("image/") && !/heic|heif/.test(mimeType ?? "");

  return (
    <>
      <button onClick={() => setOpen(true)}
        className="flex min-w-0 items-center gap-2 text-left text-label font-medium text-ink hover:text-accent">
        <Thumb url={url} isImage={isImage} mimeType={mimeType} />
        <span className="min-w-0 truncate underline decoration-txt3/40 underline-offset-2 hover:decoration-accent">
          {title}
        </span>
      </button>
      {open && (
        <Modal open onClose={() => setOpen(false)} title={title} size="lg">
          <FilePreview url={url} name={title} />
        </Modal>
      )}
    </>
  );
}

const ICONS: Record<string, string> = {
  pdf: "▤", image: "▧", docx: "▥", xlsx: "▦", text: "▤",
};

function iconFor(mimeType?: string | null): string {
  const mt = (mimeType ?? "").toLowerCase();
  if (mt.includes("pdf")) return ICONS.pdf;
  if (mt.startsWith("image/")) return ICONS.image;
  if (mt.includes("word")) return ICONS.docx;
  if (mt.includes("sheet") || mt.includes("excel")) return ICONS.xlsx;
  if (mt.startsWith("text/")) return ICONS.text;
  return "▢";
}

/** A real thumbnail for images (fetched once, small); a type glyph for everything else —
 * rendering an actual first-page thumbnail for PDF/DOCX would need pdf.js or a server-side
 * rasteriser, which is more than this affordance is worth. Exported so a caller that already
 * has its own click target (e.g. a row that navigates to a detail page on title-click) can
 * still reuse the glyph as a separate quick-preview trigger — see `Evidence.tsx`'s
 * `QuickPreview`, which wraps this in its own button rather than using `AttachmentLink`
 * whole, precisely to avoid two different things responding to one click. */
export function Thumb({ url, isImage, mimeType }: { url: string; isImage: boolean; mimeType?: string | null }) {
  const [src, setSrc] = useState("");

  useEffect(() => {
    if (!isImage) return;
    let revoke: (() => void) | null = null;
    let live = true;
    fetchBlob(`${url}${url.includes("?") ? "&" : "?"}disposition=inline`).then((b) => {
      if (!live) { b.revoke(); return; }
      revoke = b.revoke; setSrc(b.objectUrl);
    }).catch(() => {});
    return () => { live = false; revoke?.(); };
  }, [url, isImage]);

  if (isImage && src)
    return <img src={src} alt="" className="h-6 w-6 shrink-0 rounded border border-bd object-cover" />;
  return (
    <span className="grid h-6 w-6 shrink-0 place-items-center rounded border border-bd bg-canvas text-label text-txt3">
      {iconFor(mimeType)}
    </span>
  );
}
