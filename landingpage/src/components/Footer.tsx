import { Wordmark, PoweredBy } from "./Brand";

// No Privacy/Terms links — no such pages exist anywhere in webui/src/pages, so don't link to
// a 404.
export function Footer() {
  return (
    <footer className="border-t border-hair/70 px-4 py-10 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 sm:flex-row sm:justify-between">
        <Wordmark size="sm" />
        <PoweredBy />
      </div>
    </footer>
  );
}
