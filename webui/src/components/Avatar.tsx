import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { cn } from "../lib/ui";

/**
 * The two identity marks in the product: a person's initials circle, and the organisation's
 * logo (P6).
 *
 * The initials pattern was re-implemented four times — `Shell.tsx` twice, `People.tsx` twice —
 * each with its own size, radius and font weight, which is a small part of why the app read
 * as assembled rather than designed. One component, one scale.
 */

const SIZES = {
  xs: "h-6 w-6 text-micro",
  sm: "h-7 w-7 text-caption",
  md: "h-9 w-9 text-label",
  lg: "h-12 w-12 text-subtitle",
} as const;

export type AvatarSize = keyof typeof SIZES;

/** Up to two initials. Handles single-word names and stray whitespace without producing "". */
export function initialsOf(name: string | null | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts.slice(0, 2).map((p) => p[0]!).join("").toUpperCase();
}

export function Avatar({ name, size = "sm", className }:
  { name: string | null | undefined; size?: AvatarSize; className?: string }) {
  return (
    <span aria-hidden="true"
      className={cn("grid shrink-0 place-items-center rounded-full bg-ink font-semibold text-white",
        SIZES[size], className)}>
      {initialsOf(name)}
    </span>
  );
}

/**
 * The organisation's logo, falling back to its initials tile.
 *
 * Fetched as a blob rather than via a plain `<img src="/api/org/logo">` because the app
 * authenticates with an `Authorization` header and an `<img>` element cannot send one — the
 * same reason `FilePreview.tsx` does it this way.
 *
 * Having no logo is the ORDINARY case — most organisations never upload one — so the endpoint
 * answers 204, not 404, and this renders the initials tile. An earlier version 404'd, which
 * meant a failed request on every page load of the product; `01-smoke.spec.ts` asserts no
 * `/api/` response >= 400 on any route and caught it.
 */
export function OrgLogo({ name, size = "sm", rounded = "rounded-md" }:
  { name: string | null | undefined; size?: AvatarSize; rounded?: string }) {
  // react-query, not a bare useEffect: this renders TWICE on every page (header + sidebar
  // tile), so a per-component fetch meant two identical requests on every navigation.
  // One cached entry serves both, survives route changes, and gives Admin a key to
  // invalidate when the logo is replaced.
  const { data: blob } = useQuery({
    queryKey: ["org-logo"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const r = await api.get("/org/logo", { responseType: "blob" });
      // 204 => no logo set. axios still hands back an empty Blob, and an empty blob makes a
      // broken <img>, so size is what decides — not the status alone.
      const b = r.data as Blob;
      return b && b.size > 0 ? b : null;
    },
  });

  // The object URL is derived from the cached blob and revoked when it changes or the
  // component unmounts. Creating it inside queryFn would leak one per cache read.
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) { setUrl(null); return; }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  if (!url) {
    return (
      <span aria-hidden="true"
        className={cn("grid shrink-0 place-items-center bg-ink font-bold text-white",
          SIZES[size], rounded)}>
        {initialsOf(name)}
      </span>
    );
  }
  return (
    <img src={url} alt=""
      className={cn("shrink-0 border border-bd bg-paper object-contain", SIZES[size], rounded)} />
  );
}
