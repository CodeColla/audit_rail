import axios from "axios";

export const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("ar_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && !location.pathname.startsWith("/login")) {
      // Clear BOTH — the session identity lives in ar_user, so leaving it behind
      // makes the app think it's still logged in and bounce /login -> / forever.
      localStorage.removeItem("ar_token");
      localStorage.removeItem("ar_user");
      location.href = "/login";
    }
    return Promise.reject(err);
  },
);

// tiny query helper: returns the data of a GET
export const get = <T,>(url: string) => api.get<T>(url).then((r) => r.data);

/**
 * Turn any API error into a STRING safe to render.
 *
 * FastAPI answers a validation failure (422) with `detail` as a *list of dicts*
 * (`[{loc, msg, type}, …]`). Assigning that into React state and rendering it throws
 * "Objects are not valid as a React child", which unmounts the whole app — a blank
 * white page from one bad form field. Always route errors through here.
 */
export function errText(e: any, fallback = "Something went wrong."): string {
  const d = e?.response?.data?.detail;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d) && d.length) {
    return d
      .map((x: any) => {
        const loc = Array.isArray(x?.loc)
          ? x.loc.filter((p: unknown) => p !== "body" && p !== "query").join(".")
          : "";
        const msg = x?.msg ?? "is invalid";
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (d && typeof d === "object") return JSON.stringify(d);
  return fallback;
}

/**
 * Pull the real filename out of a Content-Disposition header.
 *
 * The old one-liner regex (`filename\*?=…[^";]+`) had two live bugs once server filenames
 * could contain arbitrary title text: it matched whichever `filename=`/`filename*=`
 * attribute appeared FIRST in the header rather than preferring the RFC 5987 one, and
 * `[^";]+` stopped at the first semicolon — including one that was legitimately part of a
 * quoted filename — truncating "Policy; Revised v1.0.pdf" down to "Policy". The truncated
 * (or wrong) result was then run through `decodeURIComponent`, which throws on a lone `%`
 * that isn't valid percent-encoding — silently failing the whole download.
 *
 * `filename*=UTF-8''…` is preferred and IS validly percent-encoded (the server always
 * produces it with `urllib.parse.quote`), so decoding it is safe. The plain quoted
 * `filename="…"` is used verbatim — never decoded — since it may legitimately contain a
 * literal `%`.
 */
function parseFilename(disposition: string): string | undefined {
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) {
    try { return decodeURIComponent(star[1]); } catch { /* fall through to the ascii name */ }
  }
  const quoted = disposition.match(/filename="((?:[^"\\]|\\.)*)"/i);
  if (quoted) return quoted[1].replace(/\\"/g, '"');
  return undefined;
}

/**
 * Fetch an auth-protected file as a Blob plus its object URL — the read-only sibling of
 * `downloadFile`, for previewing rather than saving.
 *
 * The caller MUST call `revoke()` when it is finished, or the blob stays in memory for the
 * life of the page.
 */
export async function fetchBlob(url: string): Promise<{
  blob: Blob; objectUrl: string; contentType: string; filename: string; revoke: () => void;
}> {
  const r = await api.get(url, { responseType: "blob" });
  const blob = r.data as Blob;
  const disposition = String(r.headers["content-disposition"] ?? "");
  const objectUrl = URL.createObjectURL(blob);
  return {
    blob,
    objectUrl,
    contentType: String(r.headers["content-type"] ?? blob.type ?? ""),
    filename: parseFilename(disposition) ?? url.split("/").pop() ?? "file",
    revoke: () => URL.revokeObjectURL(objectUrl),
  };
}

/**
 * Download a file from an auth-protected API route.
 *
 * A plain `<a href="/api/…">` CANNOT work here: the JWT lives in localStorage and is
 * only attached by the axios interceptor above, so a native browser navigation arrives
 * with no Authorization header and the API answers 401. Every export/PDF/evidence link
 * in the app must go through this instead.
 *
 * Honours the server's Content-Disposition filename when it sends one.
 */
export async function downloadFile(url: string, fallbackName?: string): Promise<void> {
  const r = await api.get(url, { responseType: "blob" });
  const disposition = String(r.headers["content-disposition"] ?? "");
  const name = parseFilename(disposition) ?? fallbackName ?? url.split("/").pop() ?? "download";

  const href = URL.createObjectURL(r.data as Blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}
