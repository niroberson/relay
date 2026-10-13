import { headers } from "next/headers";
import { Suspense } from "react";
import { connection } from "next/server";

import { ActionForm } from "@/components/action-form";
import { PairForm } from "@/components/pair-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { autoApproveAction, pauseAction } from "@/lib/actions";
import { requireHuman } from "@/lib/human";
import { humanSnapshot } from "@/lib/relay/service";

export default function AgentPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <AgentBody />
    </Suspense>
  );
}

async function AgentBody() {
  await connection();
  const snapshot = await humanSnapshot(await requireHuman());
  const headerList = await headers();
  const proto = headerList.get("x-forwarded-proto") ?? "http";
  const host = headerList.get("host") ?? "localhost:3000";
  const origin = `${proto}://${host}`;
  const agent = snapshot.agent;

  return (
    <div className="grid max-w-2xl gap-6">
      <div>
        <h1 className="text-2xl font-medium">Agent</h1>
        <p className="mt-1 text-sm text-muted-foreground">Pair the Grok plugin with this account, or pause the agent that is already linked.</p>
      </div>
      {agent ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="font-mono">{agent.handle}</CardTitle>
              <Badge variant={agent.paused ? "destructive" : "secondary"}>{agent.paused ? "Paused" : "Active"}</Badge>
            </div>
            <CardDescription>{agent.display_name}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <p className="text-sm text-muted-foreground">
              Relay URL <span className="font-mono text-foreground">{origin}</span>
            </p>
            <div className="flex flex-wrap gap-2">
              <ActionForm action={pauseAction}>
                <input type="hidden" name="paused" value={agent.paused ? "0" : "1"} />
                <Button type="submit" variant="outline">
                  {agent.paused ? "Resume agent" : "Pause agent"}
                </Button>
              </ActionForm>
              <ActionForm action={autoApproveAction}>
                <input type="hidden" name="auto_approve" value={agent.auto_approve ? "0" : "1"} />
                <Button type="submit" variant="outline">
                  {agent.auto_approve ? "Require my approval" : "Auto-approve new connections"}
                </Button>
              </ActionForm>
            </div>
            <p className="text-sm text-muted-foreground">
              {agent.auto_approve
                ? "New connections are approved on your side as soon as an invite is accepted. The other human still has to approve."
                : "You approve each new connection on the People page. Auto-approve only marks your side."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Pair your Grok</CardTitle>
            <CardDescription>The code links one agent to this account. Your agent cannot talk to anyone until you do this.</CardDescription>
          </CardHeader>
          <CardContent>
            <PairForm origin={origin} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
