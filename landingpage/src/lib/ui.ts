import { extendTailwindMerge } from "tailwind-merge";
import clsx, { ClassValue } from "clsx";

// tailwind-merge has to be told about the custom type scale, or it silently drops a text
// COLOUR class whenever it's combined with a custom text-SIZE class (its built-in "font-size"
// group only recognizes stock keys like text-sm/text-base) — same gotcha and same fix as
// webui/src/lib/ui.tsx's own cn(). Every custom size token in tailwind.config.js must be
// listed here, "hero" included, or this bites silently again.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["hero", "display", "title", "subtitle", "body", "sm", "label", "caption", "micro"] }],
    },
  },
});

export const cn = (...a: ClassValue[]) => twMerge(clsx(a));
