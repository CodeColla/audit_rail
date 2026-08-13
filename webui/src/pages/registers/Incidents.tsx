import { PageHead } from "../../lib/ui";
import { IncidentsTab } from "./Registers";

/**
 * P4-S3: Incidents is its own module now (it used to be a tab under Registers). The table and
 * drawer still live in Registers.tsx — this file is the page shell and the route target.
 */
export default function Incidents() {
  return (
    <>
      <PageHead eyebrow="Program" title="Incidents"
        lead="What went wrong, why, and what you changed so it does not happen again." />
      <IncidentsTab />
    </>
  );
}
