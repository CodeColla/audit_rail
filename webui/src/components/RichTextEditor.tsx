import { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import TextAlign from "@tiptap/extension-text-align";
import Placeholder from "@tiptap/extension-placeholder";
import {
  Bold, Italic, Underline, Strikethrough, Code, Heading1, Heading2, Heading3,
  List, ListOrdered, Quote, Minus, Link2, Table as TableIcon,
  AlignLeft, AlignCenter, AlignRight, Undo2, Redo2, SquareCode, ImagePlus,
} from "lucide-react";
import { cn } from "../lib/ui";
import { DocImage, MAX_INSERT_WIDTH, uploadDocImage } from "./DocImage";
import { IMPORT_LOSES, importDocx, type ImportResult } from "../lib/docxImport";

/**
 * The document authoring surface (P4-S4) — replaces a raw markdown <textarea>.
 *
 * Output is HTML, sanitised server-side on save (`api/html_sanitize.py`); the allow-list
 * there is deliberately a superset of what this editor can emit, so formatting is never
 * silently eaten. If you add an extension here, widen that allow-list in the same change.
 *
 * Link and Underline are NOT registered on purpose: StarterKit v3 already bundles both, and
 * adding them again registers duplicate extension names and warns at runtime.
 *
 * Images (P6-S5) come from `DocImage`, which is hand-written rather than
 * `@tiptap/extension-image` — read the note at the top of that file before touching it. The
 * short version: the node stores a file id and never a URL, because the displayed image is an
 * object URL and letting one of those reach `content` would write `blob:…` into a hashed,
 * signed audit record.
 *
 * The editor body carries `doc-md`, the same class as the read view, so authoring looks
 * like the published result rather than approximating it.
 */
export function RichTextEditor({ value, onChange, docId }: {
  value: string;
  onChange: (html: string) => void;
  /** Needed to upload images. Without it the image affordances are simply absent — better
   *  than a button that fails when pressed. */
  docId?: string;
}) {
  const [imageError, setImageError] = useState("");
  const [importing, setImporting] = useState(false);
  const [report, setReport] = useState<ImportResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const docxInput = useRef<HTMLInputElement>(null);

  /** Upload files and insert them, one at a time so a partial failure still lands the rest. */
  const insertImages = async (editor: Editor, files: File[]) => {
    setImageError("");
    for (const file of files) {
      try {
        const { fileId, width } = await uploadDocImage(docId!, file);
        editor.chain().focus().insertContent({
          type: "docImage",
          attrs: { fileId, alt: file.name,
                   width: width ? String(Math.min(width, MAX_INSERT_WIDTH)) : null },
        }).run();
      } catch (e: any) {
        setImageError(e?.response?.data?.detail ?? `could not add ${file.name}`);
      }
    }
  };

  /** Replace the draft body with a converted .docx. Destructive by nature, so it asks first —
   *  autosave will commit the replacement about a second later. */
  const runImport = async (editor: Editor, file: File) => {
    if (!window.confirm(
      "Importing replaces everything in this draft.\n\n" +
      "The current text is only recoverable through version history. Continue?")) return;
    setImporting(true);
    setImageError("");
    setReport(null);
    try {
      const result = await importDocx(docId!, file);
      editor.commands.setContent(result.html);
      onChange(editor.getHTML());
      setReport(result);
    } catch (e: any) {
      setImageError(e?.response?.data?.detail ?? "that file could not be read as a .docx");
    } finally {
      setImporting(false);
    }
  };

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false } }),
      TableKit.configure({ table: { resizable: false } }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Placeholder.configure({ placeholder: "Write the policy…" }),
      DocImage,
    ],
    content: value,
    // @tiptap/react v3 defaults this to false and skips re-rendering on selection-only
    // transactions (moving the caret, selecting text) — without it the toolbar's
    // isActive()/aria-pressed states only updated when the DOCUMENT changed, so a button
    // could sit highlighted (or not) for whatever was selected several edits ago.
    shouldRerenderOnTransaction: true,
    editorProps: {
      attributes: { class: "doc-md min-h-[58vh] p-5 outline-none" },
      // Pasting a screenshot is how anyone actually puts a picture in a policy; a file
      // picker is the fallback, not the path. Returning true CONSUMES the event, which
      // matters for Word and Excel: they put an HTML flavour on the clipboard alongside the
      // bitmap, so without this you get the image AND a stripped skeleton of their markup.
      handlePaste: (_view, event) => {
        const files = imageFiles(event.clipboardData?.files);
        if (!docId || !files.length || !editor) return false;
        event.preventDefault();
        void insertImages(editor, files);
        return true;
      },
      handleDrop: (_view, event) => {
        const files = imageFiles((event as DragEvent).dataTransfer?.files);
        if (!docId || !files.length || !editor) return false;
        event.preventDefault();
        void insertImages(editor, files);
        return true;
      },
    },
    onUpdate: ({ editor }) => onChange(editor.getHTML()),
  });

  // Accept content that arrives after mount (the version loads async) without stomping on
  // the caret: only push into the editor when the incoming value is genuinely different.
  useEffect(() => {
    if (editor && value !== editor.getHTML()) {
      editor.commands.setContent(value, { emitUpdate: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editor]);

  if (!editor) return <div className="rounded-xl border border-bd bg-paper p-5 text-sm text-txt3">Loading editor…</div>;

  return (
    <div className="overflow-hidden rounded-xl border border-bd bg-paper">
      <Toolbar editor={editor} onPickImage={docId ? () => fileInput.current?.click() : undefined} />
      {docId && (
        <input ref={fileInput} type="file" className="hidden" multiple
          accept="image/png,image/jpeg,image/gif,image/webp"
          aria-label="Insert image"
          onChange={(e) => {
            const files = imageFiles(e.target.files);
            // Snapshot before the reset — `files` is a live FileList and clearing the input
            // empties it. Same trap as the P5-S1 evidence upload.
            e.target.value = "";
            if (files.length) void insertImages(editor, files);
          }} />
      )}
      {docId && (
        <div className="flex flex-wrap items-center gap-2 border-b border-bd bg-canvas px-3 py-1.5">
          <label className="btn cursor-pointer py-1 text-label">
            {importing ? "Reading…" : "Import .docx"}
            <input ref={docxInput} type="file" className="hidden" disabled={importing}
              aria-label="Import .docx"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void runImport(editor, f);
              }} />
          </label>
          <span className="text-caption text-txt3">
            Replaces everything in this draft, and saves itself like any other edit.
          </span>
        </div>
      )}
      {imageError && (
        <div role="alert" className="border-b border-bd bg-bad-bg px-3 py-1.5 text-label text-bad">
          Could not add that image — {imageError}.
        </div>
      )}
      {report && (
        // What was lost, said out loud and immediately. A conversion this lossy that reports
        // nothing is how an author submits a half-converted policy for approval.
        /* Named, because the save indicator is also a `role="status"` — two unnamed live
           regions on one page is ambiguous for a screen reader and for any test reaching
           for one of them. */
        <div role="status" aria-label="Import notes"
          className="border-b border-bd bg-canvas px-3 py-2 text-caption text-txt2">
          <div className="flex items-center justify-between gap-2">
            <strong className="text-label text-ink">
              Imported{report.imagesImported ? ` with ${report.imagesImported} image${
                report.imagesImported === 1 ? "" : "s"}` : ""} — check it before submitting
              for approval.
            </strong>
            <button type="button" className="text-txt3 hover:text-ink"
              onClick={() => setReport(null)} aria-label="Dismiss import notes">✕</button>
          </div>
          <p className="mt-1">Word does not survive the trip intact. Not imported: {
            IMPORT_LOSES.join("; ")}.</p>
          {report.messages.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {report.messages.slice(0, 8).map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          )}
        </div>
      )}
      <EditorContent editor={editor} />
    </div>
  );
}

/** The image files out of a FileList, or an empty array. Anything else (a dragged .docx, a
 *  pasted text snippet) must fall through to TipTap's own handling rather than being eaten. */
function imageFiles(list: FileList | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
}

function Btn({ on, onClick, title, children }: {
  on?: boolean; onClick: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} title={title} aria-label={title}
      aria-pressed={!!on}
      className={cn("grid h-7 w-7 place-items-center rounded text-txt2 hover:bg-canvas hover:text-ink",
        on && "bg-[rgba(249,115,22,0.11)] text-ink")}>
      {children}
    </button>
  );
}

const Sep = () => <span className="mx-1 h-5 w-px bg-bd" />;

function Toolbar({ editor, onPickImage }: { editor: Editor; onPickImage?: () => void }) {
  // Build the chain per click, never once per render. `editor.chain().focus()` in the
  // render body executed the focus command on every re-render — stealing the caret out of
  // the Changelog input as you typed — and pinned every button to the selection that
  // existed at render time, so formatting could land on the wrong block.
  const c = () => editor.chain().focus();
  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 border-b border-bd bg-paper px-2 py-1.5">
      <Btn title="Bold" on={editor.isActive("bold")} onClick={() => c().toggleBold().run()}><Bold size={15} /></Btn>
      <Btn title="Italic" on={editor.isActive("italic")} onClick={() => c().toggleItalic().run()}><Italic size={15} /></Btn>
      <Btn title="Underline" on={editor.isActive("underline")} onClick={() => c().toggleUnderline().run()}><Underline size={15} /></Btn>
      <Btn title="Strikethrough" on={editor.isActive("strike")} onClick={() => c().toggleStrike().run()}><Strikethrough size={15} /></Btn>
      <Btn title="Inline code" on={editor.isActive("code")} onClick={() => c().toggleCode().run()}><Code size={15} /></Btn>
      <Sep />
      {([1, 2, 3] as const).map((lvl) => {
        const Icon = { 1: Heading1, 2: Heading2, 3: Heading3 }[lvl];
        return (
          <Btn key={lvl} title={`Heading ${lvl}`} on={editor.isActive("heading", { level: lvl })}
            onClick={() => c().toggleHeading({ level: lvl }).run()}><Icon size={15} /></Btn>
        );
      })}
      <Sep />
      <Btn title="Bullet list" on={editor.isActive("bulletList")} onClick={() => c().toggleBulletList().run()}><List size={15} /></Btn>
      <Btn title="Numbered list" on={editor.isActive("orderedList")} onClick={() => c().toggleOrderedList().run()}><ListOrdered size={15} /></Btn>
      <Btn title="Quote" on={editor.isActive("blockquote")} onClick={() => c().toggleBlockquote().run()}><Quote size={15} /></Btn>
      <Btn title="Code block" on={editor.isActive("codeBlock")} onClick={() => c().toggleCodeBlock().run()}><SquareCode size={15} /></Btn>
      <Sep />
      <Btn title="Align left" on={editor.isActive({ textAlign: "left" })} onClick={() => c().setTextAlign("left").run()}><AlignLeft size={15} /></Btn>
      <Btn title="Align centre" on={editor.isActive({ textAlign: "center" })} onClick={() => c().setTextAlign("center").run()}><AlignCenter size={15} /></Btn>
      <Btn title="Align right" on={editor.isActive({ textAlign: "right" })} onClick={() => c().setTextAlign("right").run()}><AlignRight size={15} /></Btn>
      <Sep />
      <Btn title="Link" on={editor.isActive("link")} onClick={() => {
        const prev = editor.getAttributes("link").href ?? "";
        const url = window.prompt("Link URL (leave empty to remove)", prev);
        if (url === null) return;
        if (!url) { c().unsetLink().run(); return; }
        c().extendMarkRange("link").setLink({ href: url }).run();
      }}><Link2 size={15} /></Btn>
      <Btn title="Insert table"
        onClick={() => c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}><TableIcon size={15} /></Btn>
      <Btn title="Horizontal rule" onClick={() => c().setHorizontalRule().run()}><Minus size={15} /></Btn>
      {/* Absent rather than disabled when there is no document to upload into — a button
          that cannot work is worse than one that is not there. */}
      {onPickImage && (
        <Btn title="Insert image" onClick={onPickImage}><ImagePlus size={15} /></Btn>
      )}
      <Sep />
      <Btn title="Undo" onClick={() => c().undo().run()}><Undo2 size={15} /></Btn>
      <Btn title="Redo" onClick={() => c().redo().run()}><Redo2 size={15} /></Btn>
    </div>
  );
}
