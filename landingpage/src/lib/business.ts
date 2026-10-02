import { SALES_EMAIL, CONTACT_PHONE } from "./env";

// Single source of truth for the legal entity behind Auditrail — read only by the legal
// pages (terms/privacy/refund/contact). Mirrors req/phase10/00-issue-19.md's "Business
// details" table; that issue is the source of truth, this is just where the pages read it
// from, so the four pages can't drift from each other.
export const BUSINESS = {
  legalName: "SR Associates",
  entityType: "an Indian sole proprietorship",
  address: "Sai Niwas, Sunita Nagar, Vadgaonsheri, Pune – 411014, Maharashtra, India",
  email: SALES_EMAIL,
  phone: CONTACT_PHONE,
  jurisdiction: "Pune, Maharashtra, India",
};
