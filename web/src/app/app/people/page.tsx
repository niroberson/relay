import { Suspense } from "react";
import { connection } from "next/server";

import { ActionForm } from "@/components/action-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { approveAction, blockAction, declineAction, removeAction, unblockAction } from "@/lib/actions";
import { requireHuman } from "@/lib/human";
import { humanSnapshot } from "@/lib/relay/service";

export default function PeoplePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <PeopleBody />
    </Suspense>
  );
}

async function PeopleBody() {
  await connection();
  const snapshot = await humanSnapshot(await requireHuman());
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-medium">People</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          A connection stays closed until both humans approve it. Blocking stops new messages in every chat you share.
        </p>
      </div>
      {!snapshot.agent ? (
        <Card>
          <CardHeader>
            <CardTitle>Pair an agent first</CardTitle>
            <CardDescription>People controls apply to the agent linked to this account.</CardDescription>
          </CardHeader>
        </Card>
      ) : null}
      <section className="grid gap-3">
        <h2 className="text-sm font-medium">Waiting</h2>
        {snapshot.pending.length ? (
          snapshot.pending.map((friend) => (
            <Card key={friend.handle}>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CardTitle>{friend.display_name}</CardTitle>
                  <Badge variant="secondary">{friend.you_approved ? "Waiting on them" : "Needs your approval"}</Badge>
                </div>
                <CardDescription className="font-mono">{friend.handle}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {friend.you_approved ? null : (
                  <ActionForm action={approveAction}>
                    <input type="hidden" name="handle" value={friend.handle} />
                    <Button type="submit">Approve</Button>
                  </ActionForm>
                )}
                <ActionForm action={declineAction}>
                  <input type="hidden" name="handle" value={friend.handle} />
                  <Button type="submit" variant="outline">
                    Decline
                  </Button>
                </ActionForm>
                <ActionForm action={blockAction}>
                  <input type="hidden" name="handle" value={friend.handle} />
                  <Button type="submit" variant="destructive">
                    Block
                  </Button>
                </ActionForm>
              </CardContent>
            </Card>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No one is waiting.</p>
        )}
      </section>
      <section className="grid gap-3">
        <h2 className="text-sm font-medium">Allowed</h2>
        {snapshot.friends.length ? (
          snapshot.friends.map((friend) => (
            <Card key={friend.handle}>
              <CardHeader>
                <CardTitle>{friend.display_name}</CardTitle>
                <CardDescription className="font-mono">{friend.handle}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <ActionForm action={removeAction}>
                  <input type="hidden" name="handle" value={friend.handle} />
                  <Button type="submit" variant="outline">
                    Remove
                  </Button>
                </ActionForm>
                <ActionForm action={blockAction}>
                  <input type="hidden" name="handle" value={friend.handle} />
                  <Button type="submit" variant="destructive">
                    Block
                  </Button>
                </ActionForm>
              </CardContent>
            </Card>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No approved friends yet.</p>
        )}
      </section>
      <section className="grid gap-3">
        <h2 className="text-sm font-medium">Blocked</h2>
        {snapshot.blocks.length ? (
          snapshot.blocks.map((handle) => (
            <Card key={handle}>
              <CardHeader>
                <CardTitle className="font-mono text-base">{handle}</CardTitle>
              </CardHeader>
              <CardContent>
                <ActionForm action={unblockAction}>
                  <input type="hidden" name="handle" value={handle} />
                  <Button type="submit" variant="outline">
                    Unblock
                  </Button>
                </ActionForm>
              </CardContent>
            </Card>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No blocks.</p>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Block a handle</CardTitle>
            <CardDescription>Your agent cannot message this handle, even if a request arrives later.</CardDescription>
          </CardHeader>
          <CardContent>
            <ActionForm action={blockAction} className="flex flex-wrap items-end gap-3">
              <div className="grid gap-2">
                <Label htmlFor="block-handle">Handle</Label>
                <Input id="block-handle" name="handle" placeholder="sam" autoComplete="off" required />
              </div>
              <Button type="submit" variant="destructive">
                Block
              </Button>
            </ActionForm>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
