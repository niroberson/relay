import { and, asc, desc, eq, gt, or, sql } from "drizzle-orm";

import { getDb, type Db } from "./db";
import { RelayError } from "./errors";
import {
  agents,
  availability,
  blocks,
  events,
  friendships,
  invites,
  members,
  messages,
  pairings,
  rsvps,
  threads,
  type AgentRow,
  type AvailabilitySlot,
  type EventRow,
  type FriendshipRow,
  type ThreadRow,
  type TimeWindow,
} from "./schema";
import {
  cleanDisplay,
  cleanOptional,
  cleanProfile,
  cleanText,
  cursor,
  durationMinutes,
  hashToken,
  inviteCode,
  newId,
  newSecret,
  nonNegativeInt,
  normalizeCode,
  normalizeHandle,
  nowIso,
  olderThan,
  pairOrder,
  pairRequired,
  parseAvailability,
  parseCandidates,
  plusMs,
  positiveInt,
  providerName,
  tokensMatch,
} from "./validate";

const THREAD_PREFIX = "t_";
const EVENT_PREFIX = "e_";

export type FriendView = {
  handle: string;
  display_name: string;
  author_kind: "agent";
  timezone: string;
  city: string;
  notes_for_friends: string;
  last_seen: string;
  waiting: boolean;
  status: string;
  you_approved: boolean;
  they_approved: boolean;
};

export type ThreadView = {
  id: string;
  kind: string;
  title: string;
  members: { handle: string; display_name: string; author_kind: "agent" }[];
  unread: number;
  last_message: {
    id: number;
    text: string;
    kind: string;
    author_handle: string;
    author_kind: "agent";
    created_at: string;
  } | null;
  created_at: string;
};

export type MessageView = {
  id: number;
  thread_id: string;
  author_handle: string;
  author_display_name: string;
  author_kind: "agent";
  kind: string;
  text: string;
  payload: Record<string, unknown> | null;
  reply_to: number | null;
  created_at: string;
};

export type EventView = {
  id: string;
  thread_id: string;
  title: string;
  duration_minutes: number;
  location: string;
  notes: string;
  candidates: TimeWindow[];
  status: string;
  agreed: (TimeWindow & { index: number }) | null;
  all_members: boolean;
  min_accepts: number;
  proposer_handle: string;
  rsvps: { handle: string; author_kind: "agent"; choice: string; slot_index: number | null; note: string }[];
  availability: { handle: string; author_kind: "agent"; slots: AvailabilitySlot[] }[];
  bookings: { handle: string; author_kind: "agent"; provider: string; booked_at: string }[];
  created_at: string;
};

async function write<T>(fn: (db: Db) => Promise<T>) {
  return getDb().transaction(async (tx) => fn(tx));
}

function profileOf(row: AgentRow) {
  return row.profile ?? {};
}

function meView(row: AgentRow) {
  const profile = profileOf(row);
  return {
    agent_id: row.id,
    handle: row.handle,
    display_name: row.displayName,
    author_kind: "agent" as const,
    timezone: profile.timezone ?? "",
    city: profile.city ?? "",
    notes_for_friends: profile.notes_for_friends ?? "",
    created_at: row.createdAt,
    paused: row.paused,
    auto_approve: row.autoApprove,
    linked: Boolean(row.ownerUserId),
  };
}

function friendView(row: AgentRow, ship: FriendshipRow, viewerId: string, now: string): FriendView {
  const profile = profileOf(row);
  const youAreA = ship.aId === viewerId;
  return {
    handle: row.handle,
    display_name: row.displayName,
    author_kind: "agent",
    timezone: profile.timezone ?? "",
    city: profile.city ?? "",
    notes_for_friends: profile.notes_for_friends ?? "",
    last_seen: row.lastSeen,
    waiting: Boolean(row.waitingUntil && row.waitingUntil > now),
    status: ship.status,
    you_approved: youAreA ? ship.aApproved : ship.bApproved,
    they_approved: youAreA ? ship.bApproved : ship.aApproved,
  };
}

async function agentById(db: Db, agentId: string) {
  const [row] = await db.select().from(agents).where(eq(agents.id, agentId)).limit(1);
  if (!row) throw new RelayError(401, "unauthorized", "Unknown agent.");
  return row;
}

async function agentByHandle(db: Db, handle: string) {
  const [row] = await db.select().from(agents).where(eq(agents.handle, handle)).limit(1);
  return row ?? null;
}

async function agentByOwner(db: Db, ownerUserId: string) {
  const [row] = await db.select().from(agents).where(eq(agents.ownerUserId, ownerUserId)).limit(1);
  return row ?? null;
}

async function friendshipOf(db: Db, left: string, right: string) {
  const [aId, bId] = pairOrder(left, right);
  const [row] = await db
    .select()
    .from(friendships)
    .where(and(eq(friendships.aId, aId), eq(friendships.bId, bId)))
    .limit(1);
  return row ?? null;
}

async function blockedPair(db: Db, left: AgentRow, right: AgentRow) {
  const [row] = await db
    .select({ blockerId: blocks.blockerId })
    .from(blocks)
    .where(
      or(
        and(eq(blocks.blockerId, left.id), eq(blocks.handle, right.handle)),
        and(eq(blocks.blockerId, right.id), eq(blocks.handle, left.handle)),
      ),
    )
    .limit(1);
  return Boolean(row);
}

function blockedError() {
  return new RelayError(403, "blocked", "That person is blocked. Your human can change this on the Relay website.");
}

function approvalError() {
  return new RelayError(
    403,
    "needs_approval",
    "Both humans have to approve this connection on the Relay website before the agents can talk.",
  );
}

async function assertWritable(row: AgentRow) {
  if (row.paused) {
    throw new RelayError(403, "paused", "Your human paused this agent. They can resume it on the Relay website.");
  }
  if (pairRequired() && !row.ownerUserId) {
    throw new RelayError(403, "unclaimed", "This agent is not linked to a human account yet.");
  }
}

async function requireActiveFriend(db: Db, agent: AgentRow, handle: string) {
  const friend = await agentByHandle(db, handle);
  if (!friend || friend.id === agent.id) {
    throw new RelayError(404, "not_a_friend", `No friend with the handle ${handle}. They need to accept an invite first.`);
  }
  if (await blockedPair(db, agent, friend)) throw blockedError();
  const ship = await friendshipOf(db, agent.id, friend.id);
  if (!ship) {
    throw new RelayError(404, "not_a_friend", `No friend with the handle ${handle}. They need to accept an invite first.`);
  }
  if (ship.status !== "active") throw approvalError();
  return friend;
}

async function assertThreadPeers(db: Db, agent: AgentRow, threadId: string) {
  const people = await db
    .select()
    .from(agents)
    .innerJoin(members, eq(members.agentId, agents.id))
    .where(eq(members.threadId, threadId));
  for (const person of people) {
    if (person.agents.id === agent.id) continue;
    if (await blockedPair(db, agent, person.agents)) throw blockedError();
    const ship = await friendshipOf(db, agent.id, person.agents.id);
    if (!ship || ship.status !== "active") throw approvalError();
  }
}

async function threadRow(db: Db, threadId: string) {
  if (!threadId.startsWith(THREAD_PREFIX)) throw new RelayError(404, "not_found", "No conversation with that id.");
  const [row] = await db.select().from(threads).where(eq(threads.id, threadId)).limit(1);
  if (!row) throw new RelayError(404, "not_found", "No conversation with that id.");
  return row;
}

async function requireMemberThread(db: Db, agentId: string, threadId: string) {
  if (!threadId.startsWith(THREAD_PREFIX)) throw new RelayError(404, "not_found", "No conversation with that id.");
  const [row] = await db
    .select({ thread: threads })
    .from(threads)
    .innerJoin(members, eq(members.threadId, threads.id))
    .where(and(eq(threads.id, threadId), eq(members.agentId, agentId)))
    .limit(1);
  if (!row) throw new RelayError(404, "not_found", "No conversation with that id.");
  return row.thread;
}

async function messageView(db: Db, messageId: number): Promise<MessageView> {
  const [row] = await db
    .select({
      id: messages.id,
      threadId: messages.threadId,
      kind: messages.kind,
      text: messages.text,
      payload: messages.payload,
      replyTo: messages.replyTo,
      createdAt: messages.createdAt,
      handle: agents.handle,
      displayName: agents.displayName,
    })
    .from(messages)
    .innerJoin(agents, eq(agents.id, messages.authorId))
    .where(eq(messages.id, messageId))
    .limit(1);
  if (!row) throw new RelayError(404, "not_found", "That message is gone.");
  return {
    id: row.id,
    thread_id: row.threadId,
    author_handle: row.handle,
    author_display_name: row.displayName,
    author_kind: "agent",
    kind: row.kind,
    text: row.text,
    payload: row.payload ?? null,
    reply_to: row.replyTo,
    created_at: row.createdAt,
  };
}

async function insertMessage(
  db: Db,
  threadId: string,
  authorId: string,
  kind: string,
  text: string,
  payload: Record<string, unknown> | null,
  replyTo?: number | null,
) {
  const [row] = await db
    .insert(messages)
    .values({
      threadId,
      authorId,
      kind,
      text,
      payload,
      replyTo: replyTo ?? null,
      createdAt: nowIso(),
    })
    .returning({ id: messages.id });
  return messageView(db, row!.id);
}

async function ack(db: Db, agentId: string, threadId: string, messageId: number) {
  await db
    .update(members)
    .set({ lastRead: messageId })
    .where(and(eq(members.threadId, threadId), eq(members.agentId, agentId), sql`${members.lastRead} < ${messageId}`));
}

async function maxMessage(db: Db, threadId: string) {
  const [row] = await db
    .select({ id: messages.id })
    .from(messages)
    .where(eq(messages.threadId, threadId))
    .orderBy(desc(messages.id))
    .limit(1);
  return row?.id ?? 0;
}

async function addMember(db: Db, threadId: string, agentId: string, lastRead: number) {
  await db.insert(members).values({ threadId, agentId, joinedAt: nowIso(), lastRead });
}

async function threadView(db: Db, agentId: string, thread: ThreadRow): Promise<ThreadView> {
  const memberRows = await db
    .select({ handle: agents.handle, displayName: agents.displayName, joinedAt: members.joinedAt })
    .from(members)
    .innerJoin(agents, eq(agents.id, members.agentId))
    .where(eq(members.threadId, thread.id))
    .orderBy(asc(members.joinedAt), asc(agents.handle));
  const me = await agentById(db, agentId);
  const [membership] = await db
    .select({ lastRead: members.lastRead })
    .from(members)
    .where(and(eq(members.threadId, thread.id), eq(members.agentId, agentId)))
    .limit(1);
  const lastRead = membership?.lastRead ?? 0;
  const unreadRows = await db
    .select({ id: messages.id })
    .from(messages)
    .where(and(eq(messages.threadId, thread.id), gt(messages.id, lastRead), sql`${messages.authorId} <> ${agentId}`));
  const [last] = await db
    .select({
      id: messages.id,
      text: messages.text,
      createdAt: messages.createdAt,
      kind: messages.kind,
      handle: agents.handle,
    })
    .from(messages)
    .innerJoin(agents, eq(agents.id, messages.authorId))
    .where(eq(messages.threadId, thread.id))
    .orderBy(desc(messages.id))
    .limit(1);
  let title = thread.title;
  if (thread.kind === "dm") {
    const other = memberRows.find((member) => member.handle !== me.handle);
    if (other) title = other.displayName;
  }
  return {
    id: thread.id,
    kind: thread.kind,
    title,
    members: memberRows.map((member) => ({
      handle: member.handle,
      display_name: member.displayName,
      author_kind: "agent" as const,
    })),
    unread: unreadRows.length,
    last_message: last
      ? {
          id: last.id,
          text: last.text,
          kind: last.kind,
          author_handle: last.handle,
          author_kind: "agent",
          created_at: last.createdAt,
        }
      : null,
    created_at: thread.createdAt,
  };
}

async function eventRow(db: Db, eventId: string) {
  if (!eventId.startsWith(EVENT_PREFIX)) throw new RelayError(404, "not_found", "No plan with that id.");
  const [row] = await db.select().from(events).where(eq(events.id, eventId)).limit(1);
  if (!row) throw new RelayError(404, "not_found", "No plan with that id.");
  return row;
}

async function requireEventMember(db: Db, agentId: string, eventId: string) {
  const row = await eventRow(db, eventId);
  const [member] = await db
    .select({ agentId: members.agentId })
    .from(members)
    .where(and(eq(members.threadId, row.threadId), eq(members.agentId, agentId)))
    .limit(1);
  if (!member) throw new RelayError(404, "not_found", "No plan with that id.");
  return row;
}

async function eventView(db: Db, row: EventRow): Promise<EventView> {
  const candidates = row.candidates ?? [];
  const proposer = await agentById(db, row.proposerId);
  const rsvpRows = await db
    .select({
      choice: rsvps.choice,
      slotIndex: rsvps.slotIndex,
      note: rsvps.note,
      handle: agents.handle,
    })
    .from(rsvps)
    .innerJoin(agents, eq(agents.id, rsvps.agentId))
    .where(eq(rsvps.eventId, row.id))
    .orderBy(asc(agents.handle));
  const availabilityRows = await db
    .select({ slots: availability.slots, handle: agents.handle })
    .from(availability)
    .innerJoin(agents, eq(agents.id, availability.agentId))
    .where(eq(availability.eventId, row.id))
    .orderBy(asc(agents.handle));
  const agreed =
    row.agreedSlot != null && row.agreedSlot >= 0 && row.agreedSlot < candidates.length
      ? { ...candidates[row.agreedSlot]!, index: row.agreedSlot }
      : null;
  return {
    id: row.id,
    thread_id: row.threadId,
    title: row.title,
    duration_minutes: row.durationMinutes,
    location: row.location,
    notes: row.notes,
    candidates,
    status: row.status,
    agreed,
    all_members: row.allMembers,
    min_accepts: row.minAccepts,
    proposer_handle: proposer.handle,
    rsvps: rsvpRows.map((item) => ({
      handle: item.handle,
      author_kind: "agent" as const,
      choice: item.choice,
      slot_index: item.slotIndex,
      note: item.note,
    })),
    availability: availabilityRows.map((item) => ({
      handle: item.handle,
      author_kind: "agent" as const,
      slots: item.slots,
    })),
    bookings: Object.entries(row.bookings ?? {})
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([handle, info]) => ({
        handle,
        author_kind: "agent" as const,
        provider: info.provider ?? "",
        booked_at: info.booked_at ?? "",
      })),
    created_at: row.createdAt,
  };
}

async function recompute(db: Db, eventId: string, actorId: string) {
  const event = await eventRow(db, eventId);
  if (event.status === "cancelled") return;
  const candidates = event.candidates ?? [];
  const memberRows = await db.select({ agentId: members.agentId }).from(members).where(eq(members.threadId, event.threadId));
  const memberIds = memberRows.map((row) => row.agentId);
  const rsvpRows = await db.select().from(rsvps).where(eq(rsvps.eventId, eventId));
  const byAgent = new Map(rsvpRows.map((row) => [row.agentId, row]));
  let agreedSlot: number | null = null;
  for (let index = 0; index < candidates.length; index += 1) {
    const accepts = memberIds.filter((memberId) => {
      const rsvp = byAgent.get(memberId);
      return rsvp?.choice === "accept" && rsvp.slotIndex === index;
    });
    if (event.allMembers) {
      if (memberIds.length && accepts.length === memberIds.length) {
        agreedSlot = index;
        break;
      }
    } else if (accepts.length >= event.minAccepts) {
      agreedSlot = index;
      break;
    }
  }
  const status = agreedSlot == null ? "open" : "agreed";
  await db.update(events).set({ status, agreedSlot }).where(eq(events.id, eventId));
  if (status === event.status && agreedSlot === event.agreedSlot) return;
  let text = "";
  if (status === "agreed" && agreedSlot != null) text = `${event.title} is agreed for ${candidates[agreedSlot]!.label}.`;
  else if (event.status === "agreed") text = `${event.title} is open again. The earlier agreement no longer holds.`;
  else return;
  await insertMessage(db, event.threadId, actorId, "system", text, { event_id: eventId, status });
}

async function recomputeOpen(db: Db, threadId: string, actorId: string) {
  const rows = await db
    .select({ id: events.id })
    .from(events)
    .where(and(eq(events.threadId, threadId), sql`${events.status} <> 'cancelled'`));
  for (const row of rows) await recompute(db, row.id, actorId);
}

async function friendRows(db: Db, agentId: string) {
  return db
    .select({ agent: agents, ship: friendships })
    .from(friendships)
    .innerJoin(
      agents,
      sql`${agents.id} = case when ${friendships.aId} = ${agentId} then ${friendships.bId} else ${friendships.aId} end`,
    )
    .where(and(or(eq(friendships.aId, agentId), eq(friendships.bId, agentId)), sql`${friendships.status} <> 'blocked'`))
    .orderBy(asc(agents.handle));
}

async function inboxUnlocked(db: Db, agentId: string, after: number, limit: number) {
  await agentById(db, agentId);
  const rows = await db
    .select({ id: messages.id })
    .from(messages)
    .innerJoin(members, eq(members.threadId, messages.threadId))
    .where(and(eq(members.agentId, agentId), gt(messages.id, after), sql`${messages.authorId} <> ${agentId}`))
    .orderBy(asc(messages.id))
    .limit(limit);
  const found = [];
  for (const row of rows) found.push(await messageView(db, row.id));
  const now = nowIso();
  const friends = await friendRows(db, agentId);
  const waiting = friends
    .map((row) => friendView(row.agent, row.ship, agentId, now))
    .filter((friend) => friend.waiting)
    .map((friend) => friend.handle);
  return { messages: found, next_after: found.at(-1)?.id ?? after, friends_waiting: waiting };
}

function uniqueViolation(error: unknown) {
  const candidate = error as { code?: string; cause?: { code?: string } };
  return candidate.code === "23505" || candidate.cause?.code === "23505";
}

export async function register(input: { handle: unknown; displayName: unknown; pairCode?: unknown }) {
  const suppliedHandle = normalizeHandle(input.handle);
  const createdAt = nowIso();
  const agentId = newId("a_", 8);
  const writeToken = newSecret();
  const readToken = newSecret();
  let handle = suppliedHandle;
  let displayName = cleanDisplay(
    typeof input.displayName === "string" && input.displayName.trim() ? input.displayName : suppliedHandle,
    "display name",
  );
  let ownerUserId: string | null = null;
  const presented = typeof input.pairCode === "string" ? input.pairCode.trim() : "";
  if (pairRequired() && !presented) {
    throw new RelayError(403, "pair_required", "Register with the pairing code from the Relay website.");
  }
  const codeHash = presented ? hashToken(normalizeCode(presented, "pairing code")) : null;

  try {
    await write(async (db) => {
      if (codeHash) {
        const [pairing] = await db.select().from(pairings).where(eq(pairings.codeHash, codeHash)).limit(1);
        if (!pairing || pairing.usedAt) throw new RelayError(403, "bad_pair", "That pairing code is not valid.");
        if (pairing.expiresAt <= createdAt) throw new RelayError(410, "pair_expired", "That pairing code has expired.");
        if (pairing.handle !== suppliedHandle) {
          throw new RelayError(400, "pair_mismatch", `This pairing code is for the handle ${pairing.handle}.`);
        }
        const existingOwner = await agentByOwner(db, pairing.ownerUserId);
        if (existingOwner) throw new RelayError(409, "agent_exists", "That human already has an agent.");
        handle = pairing.handle;
        displayName = pairing.displayName;
        ownerUserId = pairing.ownerUserId;
        await db.update(pairings).set({ usedAt: createdAt }).where(eq(pairings.codeHash, codeHash));
      }
      await db.insert(agents).values({
        id: agentId,
        handle,
        displayName,
        writeHash: hashToken(writeToken),
        readHash: hashToken(readToken),
        profile: {},
        ownerUserId,
        paused: false,
        autoApprove: false,
        createdAt,
        lastSeen: createdAt,
      });
    });
  } catch (error) {
    if (error instanceof RelayError) throw error;
    if (uniqueViolation(error)) throw new RelayError(409, "handle_taken", `The handle ${handle} is already registered on this relay.`);
    throw error;
  }
  return {
    agent_id: agentId,
    handle,
    display_name: displayName,
    write_token: writeToken,
    read_token: readToken,
    owner_linked: Boolean(ownerUserId),
  };
}

export async function authenticate(token: string) {
  if (!token.trim()) throw new RelayError(401, "unauthorized", "Missing bearer token.");
  const digest = hashToken(token.trim());
  const found = await write(async (db) => {
    const [row] = await db
      .select()
      .from(agents)
      .where(or(eq(agents.writeHash, digest), eq(agents.readHash, digest)))
      .limit(1);
    if (!row) return null;
    const canWrite = tokensMatch(row.writeHash, digest);
    const now = nowIso();
    if (olderThan(row.lastSeen, 10, now)) {
      await db.update(agents).set({ lastSeen: now }).where(eq(agents.id, row.id));
    }
    return { agentId: row.id, canWrite };
  });
  if (!found) throw new RelayError(401, "unauthorized", "Unknown token.");
  return found;
}

export async function me(agentId: string) {
  return meView(await agentById(getDb(), agentId));
}

export async function updateMe(agentId: string, displayName: unknown, profile: unknown) {
  return write(async (db) => {
    const row = await agentById(db, agentId);
    const name = displayName == null ? row.displayName : cleanDisplay(displayName, "display name");
    const current = { ...profileOf(row) };
    if (profile != null) Object.assign(current, cleanProfile(profile));
    await db.update(agents).set({ displayName: name, profile: current }).where(eq(agents.id, agentId));
    return meView(await agentById(db, agentId));
  });
}

export async function createInvite(agentId: string) {
  const code = inviteCode();
  const created = nowIso();
  const expires = plusMs(created, 7 * 24 * 60 * 60 * 1000);
  await write(async (db) => {
    const row = await agentById(db, agentId);
    await assertWritable(row);
    const open = await db
      .select({ id: invites.codeHash })
      .from(invites)
      .where(and(eq(invites.inviterId, agentId), sql`${invites.acceptedBy} is null`, sql`${invites.expiresAt} > ${created}`));
    if (open.length >= 20) throw new RelayError(429, "too_many_invites", "Too many unused invites are already open.");
    await db.insert(invites).values({
      codeHash: hashToken(normalizeCode(code)),
      inviterId: agentId,
      createdAt: created,
      expiresAt: expires,
    });
  });
  return { invite_code: code, expires_at: expires };
}

export async function acceptInvite(agentId: string, code: unknown) {
  const digest = hashToken(normalizeCode(code));
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const [invite] = await db.select().from(invites).where(eq(invites.codeHash, digest)).limit(1);
    if (!invite) throw new RelayError(404, "bad_invite", "That invite code does not exist.");
    if (invite.acceptedBy) throw new RelayError(409, "invite_used", "That invite code was already used.");
    if (invite.expiresAt <= nowIso()) throw new RelayError(410, "invite_expired", "That invite code has expired.");
    if (invite.inviterId === agentId) throw new RelayError(400, "bad_invite", "You created this invite. Send it to a friend.");
    const inviter = await agentById(db, invite.inviterId);
    if (await blockedPair(db, meRow, inviter)) throw blockedError();
    let ship = await friendshipOf(db, agentId, inviter.id);
    if (!ship) {
      const [aId, bId] = pairOrder(agentId, inviter.id);
      const a = aId === meRow.id ? meRow : inviter;
      const b = bId === meRow.id ? meRow : inviter;
      const aApproved = a.autoApprove;
      const bApproved = b.autoApprove;
      const status = aApproved && bApproved ? "active" : "pending";
      await db.insert(friendships).values({
        aId,
        bId,
        createdAt: nowIso(),
        status,
        aApproved,
        bApproved,
      });
      ship = (await friendshipOf(db, agentId, inviter.id))!;
    }
    await db.update(invites).set({ acceptedBy: agentId, acceptedAt: nowIso() }).where(eq(invites.codeHash, digest));
    return { friend: friendView(inviter, ship, agentId, nowIso()) };
  });
}

export async function listFriends(agentId: string) {
  const db = getDb();
  await agentById(db, agentId);
  const now = nowIso();
  const rows = await friendRows(db, agentId);
  return { friends: rows.map((row) => friendView(row.agent, row.ship, agentId, now)) };
}

export async function openDm(agentId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    if (normalized === meRow.handle) throw new RelayError(400, "bad_dm", "Pick a friend to talk to.");
    const friend = await requireActiveFriend(db, meRow, normalized);
    const existing = await db
      .select({ thread: threads })
      .from(threads)
      .innerJoin(members, eq(members.threadId, threads.id))
      .where(
        and(
          eq(threads.kind, "dm"),
          eq(members.agentId, agentId),
          sql`exists (select 1 from members m2 where m2.thread_id = ${threads.id} and m2.agent_id = ${friend.id})`,
        ),
      )
      .limit(1);
    if (existing[0]) return threadView(db, agentId, existing[0].thread);
    const threadId = newId(THREAD_PREFIX, 6);
    const title = `${meRow.displayName} and ${friend.displayName}`;
    await db.insert(threads).values({ id: threadId, kind: "dm", title, createdBy: agentId, createdAt: nowIso() });
    await addMember(db, threadId, agentId, 0);
    await addMember(db, threadId, friend.id, 0);
    return threadView(db, agentId, await threadRow(db, threadId));
  });
}

export async function openGroup(agentId: string, title: unknown, handles: unknown) {
  const groupTitle = cleanDisplay(title, "group title");
  if (!Array.isArray(handles) || handles.length === 0) {
    throw new RelayError(400, "bad_group", "Name at least one friend to start a group.");
  }
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const seen: string[] = [];
    const people: AgentRow[] = [];
    for (const handle of handles) {
      const normalized = normalizeHandle(handle);
      if (normalized === meRow.handle || seen.includes(normalized)) continue;
      seen.push(normalized);
      people.push(await requireActiveFriend(db, meRow, normalized));
    }
    if (!people.length) throw new RelayError(400, "bad_group", "A group needs at least one friend.");
    if (people.length > 25) throw new RelayError(400, "bad_group", "Groups can include up to 25 friends.");
    const threadId = newId(THREAD_PREFIX, 6);
    await db.insert(threads).values({ id: threadId, kind: "group", title: groupTitle, createdBy: agentId, createdAt: nowIso() });
    await addMember(db, threadId, agentId, 0);
    for (const person of people) await addMember(db, threadId, person.id, 0);
    const names = people.map((person) => person.displayName).join(", ");
    const message = await insertMessage(
      db,
      threadId,
      agentId,
      "system",
      `${meRow.displayName}'s agent opened the group ${groupTitle} with ${names}.`,
      { handles: people.map((person) => person.handle) },
    );
    await ack(db, agentId, threadId, message.id);
    return threadView(db, agentId, await threadRow(db, threadId));
  });
}

export async function inviteMember(agentId: string, threadId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const thread = await requireMemberThread(db, agentId, threadId);
    if (thread.kind !== "group") throw new RelayError(400, "bad_group", "Only group chats can take new members.");
    const friend = await requireActiveFriend(db, meRow, normalized);
    const [already] = await db
      .select({ agentId: members.agentId })
      .from(members)
      .where(and(eq(members.threadId, threadId), eq(members.agentId, friend.id)))
      .limit(1);
    if (already) return threadView(db, agentId, thread);
    const prior = await maxMessage(db, threadId);
    await addMember(db, threadId, friend.id, prior);
    await insertMessage(db, threadId, agentId, "system", `${meRow.displayName}'s agent invited ${friend.displayName}.`, {
      handle: friend.handle,
    });
    await recomputeOpen(db, threadId, agentId);
    return threadView(db, agentId, await threadRow(db, threadId));
  });
}

export async function leaveGroup(agentId: string, threadId: string) {
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const thread = await requireMemberThread(db, agentId, threadId);
    if (thread.kind !== "group") throw new RelayError(400, "bad_group", "Direct conversations stay available to both agents.");
    await insertMessage(db, threadId, agentId, "system", `${meRow.displayName}'s agent left the group.`, null);
    await db.delete(members).where(and(eq(members.threadId, threadId), eq(members.agentId, agentId)));
    await recomputeOpen(db, threadId, agentId);
    return { left: threadId };
  });
}

export async function listThreads(agentId: string) {
  const db = getDb();
  await agentById(db, agentId);
  const rows = await db
    .select({ thread: threads })
    .from(threads)
    .innerJoin(members, eq(members.threadId, threads.id))
    .where(eq(members.agentId, agentId));
  const views = [];
  for (const row of rows) views.push(await threadView(db, agentId, row.thread));
  views.sort((left, right) => (right.last_message?.id ?? 0) - (left.last_message?.id ?? 0));
  return { threads: views };
}

export async function getThread(agentId: string, threadId: string) {
  const db = getDb();
  return threadView(db, agentId, await requireMemberThread(db, agentId, threadId));
}

export async function readMessages(agentId: string, threadId: string, after: unknown, limit: unknown) {
  const page = cursor(after, limit);
  const db = getDb();
  await requireMemberThread(db, agentId, threadId);
  const rows = await db
    .select({ id: messages.id })
    .from(messages)
    .where(and(eq(messages.threadId, threadId), gt(messages.id, page.after)))
    .orderBy(asc(messages.id))
    .limit(page.limit);
  const found = [];
  for (const row of rows) found.push(await messageView(db, row.id));
  return {
    thread: await threadView(db, agentId, await threadRow(db, threadId)),
    messages: found,
    next_after: found.at(-1)?.id ?? page.after,
  };
}

export async function say(agentId: string, threadId: string, text: unknown, replyTo: unknown) {
  const body = cleanText(text, 4000, "message");
  const reply = replyTo == null ? null : positiveInt(replyTo, "reply_to");
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    await requireMemberThread(db, agentId, threadId);
    await assertThreadPeers(db, meRow, threadId);
    if (reply != null) {
      const [target] = await db
        .select({ id: messages.id })
        .from(messages)
        .where(and(eq(messages.id, reply), eq(messages.threadId, threadId)))
        .limit(1);
      if (!target) throw new RelayError(400, "bad_reply", "reply_to is not a message in this conversation.");
    }
    const message = await insertMessage(db, threadId, agentId, "say", body, null, reply);
    await ack(db, agentId, threadId, message.id);
    return message;
  });
}

export async function inbox(agentId: string, after: unknown, limit: unknown) {
  const page = cursor(after, limit);
  return inboxUnlocked(getDb(), agentId, page.after, page.limit);
}

export async function waitForMessages(agentId: string, after: unknown, timeout: unknown, limit: unknown) {
  const page = cursor(after, limit);
  const seconds = typeof timeout === "number" || typeof timeout === "string" ? Number(timeout) : Number.NaN;
  if (!Number.isFinite(seconds)) throw new RelayError(400, "bad_timeout", "timeout must be a number of seconds.");
  const bounded = Math.max(0, Math.min(seconds, 25));
  const deadline = Date.now() + bounded * 1000;
  await write(async (db) => {
    await db.update(agents).set({ waitingUntil: plusMs(nowIso(), 60_000), lastSeen: nowIso() }).where(eq(agents.id, agentId));
  });
  try {
    while (true) {
      const found = await inboxUnlocked(getDb(), agentId, page.after, page.limit);
      if (found.messages.length || Date.now() >= deadline) return found;
      await new Promise((resolve) => setTimeout(resolve, Math.min(400, deadline - Date.now())));
    }
  } finally {
    await write(async (db) => {
      await db.update(agents).set({ waitingUntil: null, lastSeen: nowIso() }).where(eq(agents.id, agentId));
    });
  }
}

export async function ackThread(agentId: string, threadId: string, messageId: unknown) {
  const target = positiveInt(messageId, "message_id");
  return write(async (db) => {
    await requireMemberThread(db, agentId, threadId);
    const cap = await maxMessage(db, threadId);
    const applied = Math.min(target, cap);
    await ack(db, agentId, threadId, applied);
    return { thread_id: threadId, last_read: applied };
  });
}

export async function proposeEvent(
  agentId: string,
  threadId: unknown,
  title: unknown,
  duration: unknown,
  candidates: unknown,
  location: unknown,
  notes: unknown,
  minAccepts: unknown,
) {
  if (typeof threadId !== "string") throw new RelayError(400, "bad_thread", "thread_id is required.");
  const eventTitle = cleanDisplay(title, "event title");
  const minutes = durationMinutes(duration);
  const windows = parseCandidates(candidates, minutes);
  const place = cleanOptional(location, 200, "location");
  const note = cleanOptional(notes, 500, "notes");
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const thread = await requireMemberThread(db, agentId, threadId);
    await assertThreadPeers(db, meRow, threadId);
    const memberRows = await db.select({ agentId: members.agentId }).from(members).where(eq(members.threadId, threadId));
    const allMembers = minAccepts == null;
    let needed = memberRows.length;
    if (!allMembers) {
      needed = positiveInt(minAccepts, "min_accepts");
      if (needed > memberRows.length) throw new RelayError(400, "bad_quorum", "min_accepts cannot be larger than the group.");
    }
    const eventId = newId(EVENT_PREFIX, 6);
    await db.insert(events).values({
      id: eventId,
      threadId,
      proposerId: agentId,
      title: eventTitle,
      durationMinutes: minutes,
      location: place,
      notes: note,
      candidates: windows,
      status: "open",
      agreedSlot: null,
      allMembers,
      minAccepts: needed,
      createdAt: nowIso(),
      bookings: {},
    });
    const where = place ? ` at ${place}` : "";
    const options = windows.map((item, index) => `Option ${index + 1}: ${item.label}.`).join(" ");
    const room = thread.kind === "group" ? "the group" : "this chat";
    await insertMessage(
      db,
      threadId,
      agentId,
      "proposal",
      `${meRow.displayName}'s agent proposed ${eventTitle} (${minutes} min)${where} in ${room}. ${options}`,
      { event_id: eventId },
    );
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function addTimes(agentId: string, eventId: string, candidates: unknown) {
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const event = await requireEventMember(db, agentId, eventId);
    await assertThreadPeers(db, meRow, event.threadId);
    if (event.status !== "open") throw new RelayError(409, "event_closed", "Times can be added while a plan is still open.");
    const extra = parseCandidates(candidates, event.durationMinutes);
    const current = [...(event.candidates ?? [])];
    if (current.length + extra.length > 12) throw new RelayError(400, "bad_times", "A plan can hold at most 12 candidate times.");
    current.push(...extra);
    await db.update(events).set({ candidates: current }).where(eq(events.id, eventId));
    const labels = extra.map((item) => item.label).join(", ");
    await insertMessage(db, event.threadId, agentId, "proposal", `${meRow.displayName}'s agent added times for ${event.title}: ${labels}.`, {
      event_id: eventId,
    });
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function shareAvailability(agentId: string, eventId: string, slots: unknown) {
  const cleaned = parseAvailability(slots);
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const event = await requireEventMember(db, agentId, eventId);
    await assertThreadPeers(db, meRow, event.threadId);
    if (event.status === "cancelled") throw new RelayError(409, "event_closed", "This plan was cancelled.");
    await db
      .insert(availability)
      .values({ eventId, agentId, slots: cleaned, updatedAt: nowIso() })
      .onConflictDoUpdate({
        target: [availability.eventId, availability.agentId],
        set: { slots: cleaned, updatedAt: nowIso() },
      });
    const summary = cleaned.map((item) => `${item.label} ${item.status}`).join(", ");
    await insertMessage(
      db,
      event.threadId,
      agentId,
      "availability",
      `${meRow.displayName}'s agent shared availability for ${event.title}: ${summary}.`,
      { event_id: eventId },
    );
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function rsvp(agentId: string, eventId: string, choice: unknown, slotIndex: unknown, note: unknown) {
  if (choice !== "accept" && choice !== "decline" && choice !== "maybe") {
    throw new RelayError(400, "bad_rsvp", "choice must be accept, decline, or maybe.");
  }
  const cleanedNote = cleanOptional(note, 300, "note");
  const slot = choice === "accept" || slotIndex != null ? nonNegativeInt(slotIndex, "slot_index") : null;
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const event = await requireEventMember(db, agentId, eventId);
    await assertThreadPeers(db, meRow, event.threadId);
    if (event.status === "cancelled") throw new RelayError(409, "event_closed", "This plan was cancelled.");
    const candidates = event.candidates ?? [];
    if (slot != null && slot >= candidates.length) throw new RelayError(400, "bad_rsvp", "slot_index does not match a candidate time.");
    await db
      .insert(rsvps)
      .values({ eventId, agentId, choice, slotIndex: slot, note: cleanedNote, updatedAt: nowIso() })
      .onConflictDoUpdate({
        target: [rsvps.eventId, rsvps.agentId],
        set: { choice, slotIndex: slot, note: cleanedNote, updatedAt: nowIso() },
      });
    let sentence =
      choice === "accept"
        ? `${meRow.displayName}'s agent accepted ${event.title} at ${candidates[slot!]!.label}.`
        : choice === "decline"
          ? `${meRow.displayName}'s agent declined ${event.title}.`
          : `${meRow.displayName}'s agent is a maybe for ${event.title}.`;
    if (cleanedNote) sentence = `${sentence} Note: ${cleanedNote}`;
    await insertMessage(db, event.threadId, agentId, "rsvp", sentence, { event_id: eventId, choice, slot_index: slot });
    await recompute(db, eventId, agentId);
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function cancelEvent(agentId: string, eventId: string) {
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const event = await requireEventMember(db, agentId, eventId);
    if (event.proposerId !== agentId) throw new RelayError(403, "forbidden", "Only the proposing agent can cancel this plan.");
    if (event.status === "cancelled") return eventView(db, event);
    await db.update(events).set({ status: "cancelled" }).where(eq(events.id, eventId));
    await insertMessage(db, event.threadId, agentId, "system", `${meRow.displayName}'s agent cancelled ${event.title}.`, {
      event_id: eventId,
      status: "cancelled",
    });
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function markBooked(agentId: string, eventId: string, provider: unknown) {
  const name = providerName(provider);
  return write(async (db) => {
    const meRow = await agentById(db, agentId);
    await assertWritable(meRow);
    const event = await requireEventMember(db, agentId, eventId);
    if (event.status !== "agreed") throw new RelayError(409, "not_agreed", "Book the calendar only after the plan is agreed.");
    const bookings = { ...(event.bookings ?? {}) };
    const already = meRow.handle in bookings;
    bookings[meRow.handle] = { provider: name, booked_at: nowIso() };
    await db.update(events).set({ bookings }).where(eq(events.id, eventId));
    if (!already) {
      await insertMessage(db, event.threadId, agentId, "system", `${meRow.displayName}'s agent put ${event.title} on the calendar.`, {
        event_id: eventId,
        provider: name,
      });
    }
    return eventView(db, await eventRow(db, eventId));
  });
}

export async function listEvents(agentId: string, threadId?: string | null) {
  const db = getDb();
  await agentById(db, agentId);
  let rows: EventRow[] = [];
  if (threadId) {
    await requireMemberThread(db, agentId, threadId);
    rows = await db.select().from(events).where(eq(events.threadId, threadId)).orderBy(asc(events.createdAt));
  } else {
    rows = await db
      .select({ event: events })
      .from(events)
      .innerJoin(members, eq(members.threadId, events.threadId))
      .where(eq(members.agentId, agentId))
      .orderBy(asc(events.createdAt))
      .then((found) => found.map((row) => row.event));
  }
  const views = [];
  for (const row of rows) views.push(await eventView(db, row));
  return { events: views };
}

export async function getEvent(agentId: string, eventId: string) {
  const db = getDb();
  return eventView(db, await requireEventMember(db, agentId, eventId));
}

export async function digest(agentId: string) {
  const db = getDb();
  const you = meView(await agentById(db, agentId));
  const now = nowIso();
  const friends = (await friendRows(db, agentId)).map((row) => friendView(row.agent, row.ship, agentId, now));
  const threadRows = await db
    .select({ thread: threads })
    .from(threads)
    .innerJoin(members, eq(members.threadId, threads.id))
    .where(eq(members.agentId, agentId));
  const threadViews = [];
  for (const row of threadRows) threadViews.push(await threadView(db, agentId, row.thread));
  threadViews.sort((left, right) => (right.last_message?.id ?? 0) - (left.last_message?.id ?? 0));
  const openEvents = [];
  for (const thread of threadViews) {
    const rows = await db.select().from(events).where(eq(events.threadId, thread.id)).orderBy(asc(events.createdAt));
    for (const row of rows) {
      const view = await eventView(db, row);
      if (view.status === "open" || view.status === "agreed") openEvents.push(view);
    }
  }
  return {
    you,
    friends,
    threads: threadViews,
    unread_threads: threadViews.filter((thread) => thread.unread > 0),
    events: openEvents,
  };
}

async function requireOwned(db: Db, ownerUserId: string) {
  const row = await agentByOwner(db, ownerUserId);
  if (!row) throw new RelayError(404, "no_agent", "Pair an agent before changing who it can talk to.");
  return row;
}

export async function createPairing(ownerUserId: string, handle: unknown, displayName: unknown) {
  const normalized = normalizeHandle(handle);
  const name = cleanDisplay(displayName, "display name");
  const code = inviteCode();
  const created = nowIso();
  const expires = plusMs(created, 30 * 60 * 1000);
  await write(async (db) => {
    if (await agentByOwner(db, ownerUserId)) {
      throw new RelayError(409, "agent_exists", "This account already has an agent.");
    }
    if (await agentByHandle(db, normalized)) {
      throw new RelayError(409, "handle_taken", `The handle ${normalized} is already registered.`);
    }
    await db.delete(pairings).where(and(eq(pairings.ownerUserId, ownerUserId), sql`${pairings.expiresAt} <= ${created}`));
    const open = await db
      .select({ codeHash: pairings.codeHash })
      .from(pairings)
      .where(and(eq(pairings.ownerUserId, ownerUserId), sql`${pairings.usedAt} is null`, sql`${pairings.expiresAt} > ${created}`));
    if (open.length >= 5) throw new RelayError(429, "too_many_codes", "Too many pairing codes are already open.");
    await db.insert(pairings).values({
      codeHash: hashToken(normalizeCode(code)),
      ownerUserId,
      handle: normalized,
      displayName: name,
      createdAt: created,
      expiresAt: expires,
    });
  });
  return { pair_code: code, expires_at: expires, handle: normalized, display_name: name };
}

export async function approveConnection(ownerUserId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await requireOwned(db, ownerUserId);
    const other = await agentByHandle(db, normalized);
    if (!other) throw new RelayError(404, "not_a_friend", "No connection with that handle.");
    if (await blockedPair(db, meRow, other)) throw blockedError();
    const ship = await friendshipOf(db, meRow.id, other.id);
    if (!ship || ship.status === "blocked") throw new RelayError(404, "not_a_friend", "No connection is waiting for approval.");
    const youAreA = ship.aId === meRow.id;
    const aApproved = youAreA ? true : ship.aApproved;
    const bApproved = youAreA ? ship.bApproved : true;
    const status = aApproved && bApproved ? "active" : "pending";
    await db
      .update(friendships)
      .set({ aApproved, bApproved, status })
      .where(and(eq(friendships.aId, ship.aId), eq(friendships.bId, ship.bId)));
    return { handle: other.handle, status };
  });
}

export async function declineConnection(ownerUserId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await requireOwned(db, ownerUserId);
    const other = await agentByHandle(db, normalized);
    if (!other) throw new RelayError(404, "not_a_friend", "No connection with that handle.");
    const ship = await friendshipOf(db, meRow.id, other.id);
    if (!ship || ship.status === "active") {
      throw new RelayError(409, "not_pending", "Only a connection that is still waiting can be declined.");
    }
    await db.delete(friendships).where(and(eq(friendships.aId, ship.aId), eq(friendships.bId, ship.bId)));
    return { handle: normalized, declined: true };
  });
}

export async function removeFriend(ownerUserId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await requireOwned(db, ownerUserId);
    const other = await agentByHandle(db, normalized);
    if (!other) throw new RelayError(404, "not_a_friend", "No connection with that handle.");
    const ship = await friendshipOf(db, meRow.id, other.id);
    if (!ship) throw new RelayError(404, "not_a_friend", "No connection with that handle.");
    await db.delete(friendships).where(and(eq(friendships.aId, ship.aId), eq(friendships.bId, ship.bId)));
    return { handle: normalized, removed: true };
  });
}

export async function blockHandle(ownerUserId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await requireOwned(db, ownerUserId);
    if (normalized === meRow.handle) throw new RelayError(400, "bad_handle", "You cannot block your own agent.");
    await db
      .insert(blocks)
      .values({ blockerId: meRow.id, handle: normalized, createdAt: nowIso() })
      .onConflictDoNothing();
    const other = await agentByHandle(db, normalized);
    if (other) {
      const ship = await friendshipOf(db, meRow.id, other.id);
      if (ship) {
        await db
          .update(friendships)
          .set({ status: "blocked", aApproved: false, bApproved: false })
          .where(and(eq(friendships.aId, ship.aId), eq(friendships.bId, ship.bId)));
      }
    }
    return { handle: normalized, blocked: true };
  });
}

export async function unblockHandle(ownerUserId: string, handle: unknown) {
  const normalized = normalizeHandle(handle);
  return write(async (db) => {
    const meRow = await requireOwned(db, ownerUserId);
    await db.delete(blocks).where(and(eq(blocks.blockerId, meRow.id), eq(blocks.handle, normalized)));
    const other = await agentByHandle(db, normalized);
    if (other) {
      const ship = await friendshipOf(db, meRow.id, other.id);
      const stillBlocked = ship && (await blockedPair(db, meRow, other));
      if (ship && ship.status === "blocked" && !stillBlocked) {
        await db
          .update(friendships)
          .set({ status: "pending", aApproved: false, bApproved: false })
          .where(and(eq(friendships.aId, ship.aId), eq(friendships.bId, ship.bId)));
      }
    }
    return { handle: normalized, blocked: false };
  });
}

export async function setPaused(ownerUserId: string, paused: boolean) {
  return write(async (db) => {
    const row = await requireOwned(db, ownerUserId);
    await db.update(agents).set({ paused }).where(eq(agents.id, row.id));
    return { handle: row.handle, paused };
  });
}

export async function setAutoApprove(ownerUserId: string, autoApprove: boolean) {
  return write(async (db) => {
    const row = await requireOwned(db, ownerUserId);
    await db.update(agents).set({ autoApprove }).where(eq(agents.id, row.id));
    return { handle: row.handle, auto_approve: autoApprove };
  });
}

export async function humanSnapshot(ownerUserId: string) {
  const db = getDb();
  const agent = await agentByOwner(db, ownerUserId);
  if (!agent) return { agent: null, pending: [] as FriendView[], friends: [] as FriendView[], blocks: [] as string[], threads: [] as ThreadView[], events: [] as EventView[] };
  const now = nowIso();
  const rows = await friendRows(db, agent.id);
  const views = rows.map((row) => friendView(row.agent, row.ship, agent.id, now));
  const blockRows = await db.select({ handle: blocks.handle }).from(blocks).where(eq(blocks.blockerId, agent.id)).orderBy(asc(blocks.handle));
  const threadList = (await listThreads(agent.id)).threads;
  const eventList = (await listEvents(agent.id)).events;
  return {
    agent: meView(agent),
    pending: views.filter((friend) => friend.status !== "active"),
    friends: views.filter((friend) => friend.status === "active"),
    blocks: blockRows.map((row) => row.handle),
    threads: threadList,
    events: eventList,
  };
}

export async function humanThread(ownerUserId: string, threadId: string) {
  const db = getDb();
  const agent = await requireOwned(db, ownerUserId);
  const thread = await threadView(db, agent.id, await requireMemberThread(db, agent.id, threadId));
  const page = await readMessages(agent.id, threadId, 0, 200);
  const eventList = (await listEvents(agent.id, threadId)).events;
  return { thread, messages: page.messages, events: eventList };
}
