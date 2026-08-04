import { useEffect, useRef, useState } from "react";
import { downloadFile, fetchBlob } from "../lib/api";

/**
 * Preview any artifact the app stores, without leaving the page.
 *
 * Dispatch by type:
 *   PDF    — the browser's own viewer in an <iframe>
 *   images — <img>
 *   DOCX   — docx-preview renders the OOXML into a div
 *   XLSX   — SheetJS converts the first sheets to HTML tables
 *   other  — an honest "can't preview this" with a download button
 *
 * Everything is fetched through axios (`fetchBlob`), because these routes need the
 * Authorization header — a bare <iframe src="/api/…"> would 401. Office rendering is
 * approximate by design: it costs no server dependency, which a headless-LibreOffice
 * pipeline would (see docs/phase4).
 */

type Kind = "pdf" | "image" | "docx" | "xlsx" | "text" | "legacy-doc" | "heic" | "unsupported";

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
   .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function classify(contentType: string, filename: string): Kind {
  const ct = (contentType || "").toLowerCase();
  const ext = (filename.split(".").pop() ?? "").toLowerCase();
  if (ct.includes("pdf") || ext === "pdf") return "pdf";
  // HEIC/HEIF — the default format on recent iPhones — must be caught BEFORE the generic
  // `image/*` branch below. It has a real `image/…` content-type and would otherwise be
  // routed into an <img> tag, which no desktop browser can decode: the upload succeeds, and
  // the preview silently shows a broken-image icon with no explanation. That reads exactly
  // as "photos don't work" even though the bytes are sitting in the vault correctly.
  if (ct.includes("heic") || ct.includes("heif") || ["heic", "heif"].includes(ext)) return "heic";
  if (ct.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext))
    return "image";
  if (ext === "docx" || ct.includes("wordprocessingml")) return "docx";
  // Legacy pre-2007 Word (.doc, application/msword) uploads fine but has no in-browser
  // renderer — docx-preview only understands the newer OOXML format. Naming it explicitly
  // here, rather than letting it fall through to the generic "unsupported" message, is the
  // difference between "we can't preview .doc" and an unexplained blank box.
  if (ext === "doc" || ct === "application/msword") return "legacy-doc";
  if (["xlsx", "xls", "csv"].includes(ext) || ct.includes("spreadsheetml") || ct.includes("ms-excel"))
    return "xlsx";
  if (ct.startsWith("text/") || ["txt", "md", "log", "json"].includes(ext)) return "text";
  return "unsupported";
}

export function FilePreview({ url, name, className }:
  { url: string; name?: string; className?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [kind, setKind] = useState<Kind>("unsupported");
  const [objectUrl, setObjectUrl] = useState("");
  const [filename, setFilename] = useState(name ?? "");
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    let revoke = () => {};
    setState("loading"); setText(""); setMessage("");

    (async () => {
      try {
        const inline = url.includes("?") ? `${url}&disposition=inline` : `${url}?disposition=inline`;
        const f = await fetchBlob(inline);
        if (cancelled) { f.revoke(); return; }
        revoke = f.revoke;
        // The server-supplied filename FIRST. `name` is the evidence TITLE at every call
        // site, and a title like "ISO 27001 Certificate" carries no extension — so this
        // used to throw away the one reliable signal and leave dispatch resting entirely
        // on whatever MIME the uploader's browser happened to send. `f.filename` comes
        // from Content-Disposition, which the API builds from files.original_name.
        const k = classify(f.contentType, f.filename || name || "");
        setKind(k); setObjectUrl(f.objectUrl); setFilename(name ?? f.filename);

        if (k === "docx") {
          const { renderAsync } = await import("docx-preview");
          if (host.current) {
            host.current.innerHTML = "";
            await renderAsync(f.blob, host.current, undefined,
              { className: "docx", inWrapper: false, ignoreWidth: true });
          }
        } else if (k === "xlsx") {
          const XLSX = await import("xlsx");
          const wb = XLSX.read(await f.blob.arrayBuffer(), { type: "array" });
          // `sn` is a worksheet name straight out of an uploaded file, and it lands in
          // innerHTML. Unescaped, a workbook with a sheet called
          //   <img src=x onerror=…>
          // (31 chars is plenty) executes script in a page whose localStorage holds the
          // JWT — stored XSS with session theft, triggered by previewing evidence someone
          // else uploaded. sheet_to_html escapes its own cell output; this interpolation
          // was ours to escape.
          const html = wb.SheetNames.slice(0, 5).map((sn) =>
            `<h4>${escapeHtml(sn)}</h4>${XLSX.utils.sheet_to_html(wb.Sheets[sn])}`).join("");
          if (host.current) host.current.innerHTML = html;
        } else if (k === "text") {
          setText((await f.blob.text()).slice(0, 200_000));
        }
        if (!cancelled) setState("ready");
      } catch (e: any) {
        if (!cancelled) {
          setMessage(e?.response?.status === 403
            ? "You do not have permission to view this file."
            : "This file could not be loaded.");
          setState("error");
        }
      }
    })();

    return () => { cancelled = true; revoke(); };
  }, [url, name]);

  const Download = () => (
    <button onClick={() => downloadFile(url, filename)} className="btn py-1.5">
      Download{filename ? ` ${filename}` : ""} ↓
    </button>
  );

  if (state === "loading")
    return <div className={className}><div className="p-6 text-sm text-txt3">Loading preview…</div></div>;

  if (state === "error")
    return (
      <div className={className}>
        <div className="rounded-md border border-dashed border-bd p-6 text-center">
          <p className="text-sm text-txt2">{message}</p>
          <div className="mt-3"><Download /></div>
        </div>
      </div>
    );

  return (
    <div className={className}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="truncate text-label font-medium">{filename}</span>
        <Download />
      </div>

      {kind === "pdf" && (
        <iframe title={filename} src={objectUrl}
          className="h-[70vh] w-full rounded-md border border-bd bg-paper" />
      )}
      {kind === "image" && (
        <img src={objectUrl} alt={filename}
          className="max-h-[70vh] w-auto rounded-md border border-bd bg-paper" />
      )}
      {(kind === "docx" || kind === "xlsx") && (
        <div ref={host}
          className="file-preview max-h-[70vh] overflow-auto rounded-md border border-bd bg-paper p-4" />
      )}
      {kind === "text" && (
        <pre className="max-h-[70vh] overflow-auto rounded-md border border-bd bg-paper p-4 font-mono text-label leading-relaxed">
          {text}
        </pre>
      )}
      {kind === "legacy-doc" && (
        <div className="rounded-md border border-dashed border-bd p-6 text-center text-sm text-txt2">
          This is a legacy Word document (.doc) — the file uploaded correctly, but this
          older format can't be previewed in the browser. Download it to view.
        </div>
      )}
      {kind === "heic" && (
        <div className="rounded-md border border-dashed border-bd p-6 text-center text-sm text-txt2">
          This photo uploaded correctly, but its format (HEIC — the default on recent
          iPhones) can't be displayed in a browser. Download it to view, or set your phone
          to save photos as JPEG for one that will preview here.
        </div>
      )}
      {kind === "unsupported" && (
        <div className="rounded-md border border-dashed border-bd p-6 text-center text-sm text-txt2">
          There's no in-page preview for this file type — download it to view.
        </div>
      )}
    </div>
  );
}
