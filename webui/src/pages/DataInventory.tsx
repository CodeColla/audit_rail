import { PageHead } from "../lib/ui";
import { DataTab } from "./Registers";

/**
 * P4-S3: Data inventory is its own module now (it used to be a tab under Registers). The table and
 * drawer still live in Registers.tsx — this file is the page shell and the route target.
 */
export default function DataInventory() {
  return (
    <>
      <PageHead eyebrow="Program" title="Data inventory"
        lead="The kinds of data you hold and how each is classified. Assets point at these, so a bank can see what sits where." />
      <DataTab />
    </>
  );
}
