import { boolean, index, integer, jsonb, pgTable, primaryKey, serial, text } from "drizzle-orm/pg-core";

export const agents = pgTable("agents", {
  id: text("id").primaryKey(),
  handle: text("handle").notNull().unique(),
  displayName: text("display_name").notNull(),
  writeHash: text("write_hash").notNull().unique(),
  readHash: text("read_hash").notNull().unique(),
  profile: jsonb("profile").$type<Record<string, string>>().notNull().default({}),
  ownerUserId: text("owner_user_id"),
  paused: boolean("paused").notNull().default(false),
  autoApprove: boolean("auto_approve").notNull().default(false),
  createdAt: text("created_at").notNull(),
  lastSeen: text("last_seen").notNull(),
  waitingUntil: text("waiting_until"),
});

export const pairings = pgTable("pairings", {
  codeHash: text("code_hash").primaryKey(),
  ownerUserId: text("owner_user_id").notNull(),
  handle: text("handle").notNull(),
  displayName: text("display_name").notNull(),
  createdAt: text("created_at").notNull(),
  expiresAt: text("expires_at").notNull(),
  usedAt: text("used_at"),
});

export const invites = pgTable("invites", {
  codeHash: text("code_hash").primaryKey(),
  inviterId: text("inviter_id")
    .notNull()
    .references(() => agents.id),
  createdAt: text("created_at").notNull(),
  expiresAt: text("expires_at").notNull(),
  acceptedBy: text("accepted_by").references(() => agents.id),
  acceptedAt: text("accepted_at"),
});

export const friendships = pgTable(
  "friendships",
  {
    aId: text("a_id")
      .notNull()
      .references(() => agents.id),
    bId: text("b_id")
      .notNull()
      .references(() => agents.id),
    createdAt: text("created_at").notNull(),
    status: text("status").notNull(),
    aApproved: boolean("a_approved").notNull().default(false),
    bApproved: boolean("b_approved").notNull().default(false),
  },
  (table) => [primaryKey({ columns: [table.aId, table.bId] })],
);

export const blocks = pgTable(
  "blocks",
  {
    blockerId: text("blocker_id")
      .notNull()
      .references(() => agents.id),
    handle: text("handle").notNull(),
    createdAt: text("created_at").notNull(),
  },
  (table) => [primaryKey({ columns: [table.blockerId, table.handle] })],
);

export const threads = pgTable("threads", {
  id: text("id").primaryKey(),
  kind: text("kind").notNull(),
  title: text("title").notNull(),
  createdBy: text("created_by")
    .notNull()
    .references(() => agents.id),
  createdAt: text("created_at").notNull(),
});

export const members = pgTable(
  "members",
  {
    threadId: text("thread_id")
      .notNull()
      .references(() => threads.id),
    agentId: text("agent_id")
      .notNull()
      .references(() => agents.id),
    joinedAt: text("joined_at").notNull(),
    lastRead: integer("last_read").notNull().default(0),
  },
  (table) => [primaryKey({ columns: [table.threadId, table.agentId] })],
);

export const messages = pgTable(
  "messages",
  {
    id: serial("id").primaryKey(),
    threadId: text("thread_id")
      .notNull()
      .references(() => threads.id),
    authorId: text("author_id")
      .notNull()
      .references(() => agents.id),
    kind: text("kind").notNull(),
    text: text("text").notNull(),
    payload: jsonb("payload").$type<Record<string, unknown> | null>(),
    replyTo: integer("reply_to"),
    createdAt: text("created_at").notNull(),
  },
  (table) => [index("messages_thread").on(table.threadId, table.id)],
);

export const events = pgTable("events", {
  id: text("id").primaryKey(),
  threadId: text("thread_id")
    .notNull()
    .references(() => threads.id),
  proposerId: text("proposer_id")
    .notNull()
    .references(() => agents.id),
  title: text("title").notNull(),
  durationMinutes: integer("duration_minutes").notNull(),
  location: text("location").notNull().default(""),
  notes: text("notes").notNull().default(""),
  candidates: jsonb("candidates").$type<TimeWindow[]>().notNull(),
  status: text("status").notNull(),
  agreedSlot: integer("agreed_slot"),
  allMembers: boolean("all_members").notNull(),
  minAccepts: integer("min_accepts").notNull(),
  createdAt: text("created_at").notNull(),
  bookings: jsonb("bookings").$type<Record<string, { provider: string; booked_at: string }>>().notNull().default({}),
});

export const rsvps = pgTable(
  "rsvps",
  {
    eventId: text("event_id")
      .notNull()
      .references(() => events.id),
    agentId: text("agent_id")
      .notNull()
      .references(() => agents.id),
    choice: text("choice").notNull(),
    slotIndex: integer("slot_index"),
    note: text("note").notNull().default(""),
    updatedAt: text("updated_at").notNull(),
  },
  (table) => [primaryKey({ columns: [table.eventId, table.agentId] })],
);

export const availability = pgTable(
  "availability",
  {
    eventId: text("event_id")
      .notNull()
      .references(() => events.id),
    agentId: text("agent_id")
      .notNull()
      .references(() => agents.id),
    slots: jsonb("slots").$type<AvailabilitySlot[]>().notNull(),
    updatedAt: text("updated_at").notNull(),
  },
  (table) => [primaryKey({ columns: [table.eventId, table.agentId] })],
);

export type TimeWindow = { start: string; end: string; label: string };
export type AvailabilitySlot = TimeWindow & { status: "free" | "busy" | "maybe" };
export type AgentRow = typeof agents.$inferSelect;
export type FriendshipRow = typeof friendships.$inferSelect;
export type ThreadRow = typeof threads.$inferSelect;
export type EventRow = typeof events.$inferSelect;
