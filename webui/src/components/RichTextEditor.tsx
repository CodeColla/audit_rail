import { useEffect } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import TextAlign from "@tiptap/extension-text-align";
import Placeholder from "@tiptap/extension-placeholder";
import {
  Bold, Italic, Underline, Strikethrough, Code, Heading1, Heading2, Heading3,
  List, ListOrdered, Quote, Minus, Link2, Table as TableIcon,
  AlignLeft, AlignCenter, AlignRight, Undo2, Redo2, SquareCode,
} from "lucide-react";
import { cn } from "../lib/ui";

/**
 * The document authoring surface (P4-S4) — replaces a raw markdown <textarea>.
 *
 * Output is HTML, sanitised server-side on save (`api/html_sanitize.py`); the allow-list
 * there is deliberately a superset of what this editor can emit, so formatting is never
 * silently eaten. If you add an extension here, widen that allow-list in the same change.
 *
 * Two things NOT registered, on purpose:
 *   • Link and Underline — StarterKit v3 already bundles both; adding them again registers
 *     duplicate extension names and TipTap warns at runtime.
 *   • Image — `img` is off the sanitiser's allow-list because xhtml2pdf resolves image URLs
 *     with a server-side urlopen() at publish time. Images need an owned image store first.
 *
 * The editor body carries `doc-md`, the same class as the read view, so authoring looks
 * like the published result rather than approximating it.
 */
export function RichTextEditor({ value, onChange }: {
  value: string;
  onChange: (html: string) => void;
}) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false } }),
      TableKit.configure({ table: { resizable: false } }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Placeholder.configure({ placeholder: "Write the policy…" }),
    ],
    content: value,
    editorProps: { attributes: { class: "doc-md min-h-[58vh] p-5 outline-none" } },
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

  if (!editor) return <div className="rounded-xl border border-bd bg-paper p-5 text-[13px] text-txt3">Loading editor…</div>;

  return (
    <div className="overflow-hidden rounded-xl border border-bd bg-paper">
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
    </div>
  );
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

function Toolbar({ editor }: { editor: Editor }) {
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
      <Sep />
      <Btn title="Undo" onClick={() => c().undo().run()}><Undo2 size={15} /></Btn>
      <Btn title="Redo" onClick={() => c().redo().run()}><Redo2 size={15} /></Btn>
    </div>
  );
}
