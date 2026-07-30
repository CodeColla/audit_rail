import { PageHead } from "../lib/ui";
import { RisksTab } from "./Registers";

/**
 * P4-S3: Risks is its own module now (it used to be a tab under Registers). The table and
 * drawer still live in Registers.tsx — this file is the page shell and the route target.
 */
export default function Risks() {
  return (
    <>
      <PageHead eyebrow="Program" title="Risks"
        lead="Every risk you have identified — scored before and after treatment, owned by a person, and linked to the controls that address it." />
      <RisksTab />
    </>
  );
}
