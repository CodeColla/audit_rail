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
  const fromHeader = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)?.[1];
  const objectUrl = URL.createObjectURL(blob);
  return {
    blob,
    objectUrl,
    contentType: String(r.headers["content-type"] ?? blob.type ?? ""),
    filename: decodeURIComponent(fromHeader ?? url.split("/").pop() ?? "file"),
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
  const fromHeader = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)?.[1];
  const name = decodeURIComponent(fromHeader ?? fallbackName ?? url.split("/").pop() ?? "download");

  const href = URL.createObjectURL(r.data as Blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}
