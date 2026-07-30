import { PageHead } from "../lib/ui";
import { AssetsTab } from "./Registers";

/**
 * P4-S3: Assets is its own module now (it used to be a tab under Registers). The table and
 * drawer still live in Registers.tsx — this file is the page shell and the route target.
 */
export default function Assets() {
  return (
    <>
      <PageHead eyebrow="Program" title="Assets"
        lead="What you run and rely on — servers, laptops, virtual machines and services — with criticality, ownership and the data each one holds." />
      <AssetsTab />
    </>
  );
}
