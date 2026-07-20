import { PageHead, Card } from "../lib/ui";
import { useAuth } from "../lib/auth";

export default function Admin() {
  const { user } = useAuth();
  return (
    <>
      <PageHead eyebrow="Administration" title="Admin"
        lead="Team, roles, and the external auditors granted temporary access to a single assessment." />
      <Card className="max-w-lg">
        <div className="eyebrow mb-2">Signed in as</div>
        <div className="text-[15px] font-semibold">{user?.full_name}</div>
        <div className="text-[13px] capitalize text-txt2">{user?.role}</div>
        <p className="mt-4 text-[13px] text-txt3">
          Member management, auditor guest invites, and scoring configuration are exposed by the API
          (<span className="font-mono">/assessments/&#123;id&#125;/guests</span>, <span className="font-mono">/templates/&#123;id&#125;/scoring</span>)
          and land here in a later UI pass.
        </p>
      </Card>
    </>
  );
}
