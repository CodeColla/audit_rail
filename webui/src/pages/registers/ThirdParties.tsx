import { PageHead } from "../../lib/ui";
import { ThirdPartiesTab } from "./Registers";

/**
 * P4-S3: Third parties is its own module now (it used to be a tab under Registers). The table and
 * drawer still live in Registers.tsx — this file is the page shell and the route target.
 */
export default function ThirdParties() {
  return (
    <>
      <PageHead eyebrow="Program" title="Third parties"
        lead="Who you depend on, their own sub-processors (the bank's fourth parties), the agreements in force, and when each was last assessed." />
      <ThirdPartiesTab />
    </>
  );
}
