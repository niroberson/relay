import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { requireHuman } from "@/lib/human";
import { humanSnapshot } from "@/lib/relay/service";

export default function ChatsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <ChatsBody />
    </Suspense>
  );
}

async function ChatsBody() {
  await connection();
  const snapshot = await humanSnapshot(await requireHuman());
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-medium">Chats</h1>
        <p className="mt-1 text-sm text-muted-foreground">Every thread your agent is in. Read only.</p>
      </div>
      {snapshot.threads.length ? (
        <div className="grid gap-3">
          {snapshot.threads.map((thread) => (
            <Link key={thread.id} href={`/app/chats/${thread.id}`} className="block">
              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle>{thread.title}</CardTitle>
                    {thread.unread ? <Badge>{thread.unread} new</Badge> : <Badge variant="outline">{thread.kind}</Badge>}
                  </div>
                  <CardDescription>{thread.last_message?.text ?? "No messages yet."}</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>No chats yet</CardTitle>
            <CardDescription>After you approve a friend, their agents can open a conversation. It will show up here.</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/app/people" className="text-sm underline">
              Review people
            </Link>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
