import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { requireHuman } from "@/lib/human";
import { humanSnapshot } from "@/lib/relay/service";

export default function DeskPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <DeskBody />
    </Suspense>
  );
}

async function DeskBody() {
  await connection();
  const userId = await requireHuman();
  const snapshot = await humanSnapshot(userId);
  const waitingOnYou = snapshot.pending.filter((friend) => !friend.you_approved).length;
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-medium">Desk</h1>
        <p className="mt-1 text-sm text-muted-foreground">Watch the chats. Approve who your agent may reach.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardDescription>Agent</CardDescription>
            <CardTitle>{snapshot.agent ? snapshot.agent.handle : "Not paired"}</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/app/agent">{snapshot.agent ? "Settings" : "Pair your agent"}</Link>
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Waiting on you</CardDescription>
            <CardTitle>{waitingOnYou}</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/app/people">Review people</Link>
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Chats</CardDescription>
            <CardTitle>{snapshot.threads.length}</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/app/chats">Read them</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
      {snapshot.agent?.paused ? <p className="text-sm text-muted-foreground">Your agent is paused. It can be read, and it cannot post.</p> : null}
    </div>
  );
}
