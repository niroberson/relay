import { eq } from "drizzle-orm";

import { getDb } from "../src/lib/relay/db";
import {
  acceptInvite,
  approveConnection,
  blockHandle,
  createInvite,
  createPairing,
  humanThread,
  openDm,
  proposeEvent,
  register,
  say,
  setPaused,
  shareAvailability,
} from "../src/lib/relay/service";
import { agents, availability, blocks, events, friendships, invites, members, messages, pairings, rsvps, threads } from "../src/lib/relay/schema";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

async function cleanup(ownerIds: string[], handles: string[]) {
  const db = getDb();
  const rows = await db.select({ id: agents.id }).from(agents).where(eq(agents.handle, handles[0] ?? ""));
  const extra = await db.select({ id: agents.id }).from(agents).where(eq(agents.handle, handles[1] ?? ""));
  const ids = [...rows, ...extra].map((row) => row.id);
  for (const id of ids) {
    await db.delete(rsvps).where(eq(rsvps.agentId, id));
    await db.delete(availability).where(eq(availability.agentId, id));
    await db.delete(blocks).where(eq(blocks.blockerId, id));
    await db.delete(members).where(eq(members.agentId, id));
    await db.delete(messages).where(eq(messages.authorId, id));
  }
  const threadRows = ids.length
    ? await db.select({ id: threads.id }).from(threads)
    : [];
  for (const thread of threadRows) {
    const member = await db.select({ agentId: members.agentId }).from(members).where(eq(members.threadId, thread.id)).limit(1);
    if (!member.length) {
      await db.delete(events).where(eq(events.threadId, thread.id));
      await db.delete(messages).where(eq(messages.threadId, thread.id));
      await db.delete(threads).where(eq(threads.id, thread.id));
    }
  }
  for (const id of ids) {
    await db.delete(events).where(eq(events.proposerId, id));
    await db.delete(invites).where(eq(invites.inviterId, id));
    await db.delete(friendships).where(eq(friendships.aId, id));
    await db.delete(friendships).where(eq(friendships.bId, id));
    await db.delete(agents).where(eq(agents.id, id));
  }
  for (const ownerId of ownerIds) await db.delete(pairings).where(eq(pairings.ownerUserId, ownerId));
}

async function main() {
  const stamp = Math.random().toString(16).slice(2, 8);
  const handleA = `qa${stamp}a`;
  const handleB = `qa${stamp}b`;
  const ownerA = `owner-${stamp}-a`;
  const ownerB = `owner-${stamp}-b`;
  try {
    const pairA = await createPairing(ownerA, handleA, "Ada");
    const pairB = await createPairing(ownerB, handleB, "Bea");
    const agentA = await register({ handle: handleA, displayName: "Nope", pairCode: pairA.pair_code });
    const agentB = await register({ handle: handleB, displayName: "Nope", pairCode: pairB.pair_code });
    assert(agentA.display_name === "Ada", "pairing display name should win");
    assert(agentA.owner_linked && agentB.owner_linked, "both agents should link to a human");

    const invite = await createInvite(agentA.agent_id);
    const accepted = await acceptInvite(agentB.agent_id, invite.invite_code);
    assert(accepted.friend.status === "pending", "friendship should wait for both humans");

    let denied = false;
    try {
      await openDm(agentA.agent_id, handleB);
    } catch (error) {
      denied = (error as { code?: string }).code === "needs_approval";
    }
    assert(denied, "agents cannot talk before both humans approve");

    await approveConnection(ownerA, handleB);
    let stillDenied = false;
    try {
      await openDm(agentB.agent_id, handleA);
    } catch (error) {
      stillDenied = (error as { code?: string }).code === "needs_approval";
    }
    assert(stillDenied, "one approval is not enough");
    await approveConnection(ownerB, handleA);

    const thread = await openDm(agentA.agent_id, handleB);
    const message = await say(agentA.agent_id, thread.id, "Ada can do Wednesday.", null);
    const event = await proposeEvent(
      agentA.agent_id,
      thread.id,
      "Dinner",
      60,
      [{ start: "2026-10-14T18:00:00-05:00", end: "2026-10-14T19:00:00-05:00" }],
      "",
      "",
      null,
    );
    let privateFields = false;
    try {
      await shareAvailability(agentA.agent_id, event.id, [
        { start: "2026-10-14T18:00:00-05:00", end: "2026-10-14T19:00:00-05:00", status: "free", title: "dentist" },
      ]);
    } catch (error) {
      privateFields = (error as { code?: string }).code === "private_fields";
    }
    assert(privateFields, "availability rejects calendar titles and other private fields");
    const watched = await humanThread(ownerB, thread.id);
    assert(watched.messages.some((item) => item.id === message.id), "the other human can read the chat");
    assert(watched.messages.every((item) => item.author_kind === "agent"), "authors stay agents");

    await blockHandle(ownerB, handleA);
    let blocked = false;
    try {
      await say(agentA.agent_id, thread.id, "This should not send.", null);
    } catch (error) {
      blocked = (error as { code?: string }).code === "blocked";
    }
    assert(blocked, "a block stops new messages");
    const afterBlock = await humanThread(ownerA, thread.id);
    assert(afterBlock.messages.some((item) => item.text === "Ada can do Wednesday."), "history stays readable");

    await setPaused(ownerA, true);
    let paused = false;
    try {
      await createInvite(agentA.agent_id);
    } catch (error) {
      paused = (error as { code?: string }).code === "paused";
    }
    assert(paused, "a paused agent cannot invite");
    console.log("relay checks passed");
  } finally {
    await cleanup([ownerA, ownerB], [handleA, handleB]);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
