import { useEffect, useState } from "react";

/**
 * Hold a rapidly-changing value still for `ms` after the last change.
 *
 * Needed the moment a search box drives a react-query key. `main.tsx` sets no `staleTime`,
 * so v5's default of 0 makes every new key immediately stale — binding a raw input value
 * into the key fires one request per keystroke, and the responses race: the reply to "fir"
 * can land after the reply to "firewall" and repaint the older result.
 *
 * The repo had no debounce of any kind and no dependency that provides one, so this is it.
 */
export function useDebounced<T>(value: T, ms = 250): T {
  const [held, setHeld] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setHeld(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return held;
}
