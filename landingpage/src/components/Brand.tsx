import { cn } from "../lib/ui";
import logo from "../assets/brand/auditrail-logo.svg";

// PRODUCT_NAME/VENDOR: originally ported from webui/src/components/Brand.tsx's typographic
// Wordmark. Superseded here (landing page only — webui's own Brand.tsx is untouched) once a
// real designed logo existed to render instead of type. Kept for PoweredBy and for the
// image's alt text.

export const PRODUCT_NAME = "Auditrail";
export const VENDOR = "SR";

type Size = "sm" | "md" | "lg";

// Pixel heights, not the type-scale tokens the old text version used — this is an image with
// its own fixed aspect ratio (roughly 2.3:1), not a line of type.
const HEIGHT: Record<Size, string> = {
  sm: "h-7",
  md: "h-9",
  lg: "h-14",
};

export function Wordmark({ size = "sm", className }: { size?: Size; className?: string }) {
  return <img src={logo} alt={PRODUCT_NAME} className={cn("w-auto", HEIGHT[size], className)} />;
}

export function PoweredBy({ className }: { className?: string }) {
  return (
    <p className={cn("text-micro text-txt2", className)}>
      Powered by <span className="font-semibold tracking-normal">{VENDOR}</span> · © {new Date().getFullYear()}
    </p>
  );
}
