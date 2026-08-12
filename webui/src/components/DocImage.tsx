import { useEffect, useState } from "react";
import {
  Node, mergeAttributes, NodeViewWrapper, ReactNodeViewRenderer,
} from "@tiptap/react";
import { api, fetchBlob } from "../lib/api";

/**
 * The image node for `RichTextEditor` (P6-S5).
 *
 * **Hand-written rather than `@tiptap/extension-image`, for one reason that matters.** In the
 * stock extension the `src` IS the node attribute, so `editor.getHTML()` serialises whatever
 * `src` currently holds. But an `<img src="/api/…">` cannot render in this app at all: the JWT
 * lives in localStorage and is attached only by the axios interceptor, so the browser's own
 * image request arrives unauthenticated and 401s. The image therefore has to be blob-fetched
 * and shown from an object URL — and with the stock extension, making that object URL stick
 * means writing `blob:http://localhost/8f3a-…` into the attribute. That string is then PATCHed
 * into `content` by the 1.2s autosave and folded into `content_sha256`, which backs an
 * electronic signature. A blob URL is meaningless in any other tab, in any other session, and
 * for ever after — so that is unrecoverable corruption of an audit record.
 *
 * The fix is structural, not careful coding: **the node stores a `fileId`, never a URL.**
 * `renderHTML` (what `getHTML()` serialises) rebuilds the canonical `/api/documents/images/…`
 * src from it, and the object URL lives only inside a DOM node the node view owns. There is no
 * code path by which a `blob:` URL can reach storage, because no attribute holds one.
 *
 * `parseHTML` returns `false` for any src that is not ours, so a foreign `<img>` pasted from a
 * web page does not become a node at all. That is the same rule the server enforces in
 * `api/html_sanitize.py`, applied early so the author finds out immediately rather than
 * watching the picture disappear on the next save.
 */

/** Mirrors `IMG_SRC_RE` in api/html_sanitize.py — the two must accept the same shape. */
export const DOC_IMAGE_SRC = new RegExp(
  "^/api/documents/images/" +
  "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$");

/** The canonical src stored in the document — what `IMG_SRC_RE` accepts server-side. */
export const docImageUrl = (fileId: string) => `/api/documents/images/${fileId}`;

/** The same route as axios sees it. The axios instance carries `baseURL: "/api"`, so passing
 *  `docImageUrl()` to it would request `/api/api/documents/images/…`. Two spellings of one
 *  route is a smell, but the stored value must be absolute (the server matches on it) and the
 *  fetched value must be relative — so they are named apart rather than derived by trimming. */
export const docImageFetchPath = (fileId: string) => `/documents/images/${fileId}`;

/** POST an image and get back the id the document will reference. */
export async function uploadDocImage(docId: string, file: File): Promise<{
  fileId: string; width?: number; height?: number;
}> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post(`/documents/${docId}/images`, form);
  return { fileId: data.file_id, width: data.width, height: data.height };
}

/** The widest an inserted image should start. A 4000px screenshot inserted at its intrinsic
 *  size looks fine in the editor (CSS fits it) and then blows out the PDF, where the width
 *  attribute is honoured — so it is clamped at insert time, not at render time. */
export const MAX_INSERT_WIDTH = 640;

function DocImageView(props: any) {
  const { fileId, alt, title, width } = props.node.attrs;
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!fileId) return;
    let live = true;
    let revoke: (() => void) | null = null;
    fetchBlob(docImageFetchPath(fileId))
      .then((got) => {
        if (!live) { got.revoke(); return; }
        revoke = got.revoke;
        setSrc(got.objectUrl);
      })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; revoke?.(); };
  }, [fileId]);

  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      {failed ? (
        <span className="inline-block rounded border border-dashed border-bd px-2 py-1
                         text-caption text-txt3">
          Image unavailable{alt ? ` — ${alt}` : ""}
        </span>
      ) : (
        // `data-file-id` is for tests and debugging: the rendered src is a blob URL, so it is
        // not something a spec can assert the identity of.
        <img src={src ?? undefined} alt={alt ?? ""} title={title ?? undefined}
          data-file-id={fileId} width={width ?? undefined}
          className={cnImg(props.selected)} />
      )}
    </NodeViewWrapper>
  );
}

const cnImg = (selected: boolean) =>
  "max-w-full rounded" + (selected ? " outline outline-2 outline-accent" : "");

export const DocImage = Node.create({
  name: "docImage",
  group: "inline",
  inline: true,
  atom: true,
  draggable: true,

  addAttributes() {
    return {
      // The identity of the picture. NOT a URL — see the note at the top of this file.
      fileId: { default: null },
      alt: { default: null },
      title: { default: null },
      width: { default: null },
    };
  },

  parseHTML() {
    return [{
      tag: "img[src]",
      getAttrs: (el: any) => {
        const found = DOC_IMAGE_SRC.exec(el.getAttribute("src") ?? "");
        // `false` rejects the element outright, so a foreign image pasted from the web never
        // becomes a node — matching what the server would do to it on save anyway.
        if (!found) return false;
        const width = parseInt(el.getAttribute("width") ?? "", 10);
        return {
          fileId: found[1],
          alt: el.getAttribute("alt"),
          title: el.getAttribute("title"),
          width: Number.isFinite(width) && width > 0 ? String(width) : null,
        };
      },
    }];
  },

  renderHTML({ HTMLAttributes, node }) {
    // THIS is what `editor.getHTML()` writes into `content`. It is built from `fileId`, so it
    // is always the canonical route URL and never the object URL the node view is displaying.
    return ["img", mergeAttributes(HTMLAttributes, {
      src: docImageUrl(node.attrs.fileId),
      alt: node.attrs.alt ?? undefined,
      title: node.attrs.title ?? undefined,
      width: node.attrs.width ?? undefined,
      // strip the internal attribute from the serialised output
      fileId: undefined,
    })];
  },

  addNodeView() {
    return ReactNodeViewRenderer(DocImageView);
  },
});
