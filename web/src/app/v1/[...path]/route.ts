import { RelayError } from "@/lib/relay/errors";
import {
  acceptInvite,
  ackThread,
  addTimes,
  authenticate,
  cancelEvent,
  createInvite,
  digest,
  getEvent,
  getThread,
  inbox,
  inviteMember,
  leaveGroup,
  listEvents,
  listFriends,
  listThreads,
  markBooked,
  me,
  openDm,
  openGroup,
  proposeEvent,
  readMessages,
  register,
  rsvp,
  say,
  shareAvailability,
  updateMe,
  waitForMessages,
} from "@/lib/relay/service";
import { joinKeyOk } from "@/lib/relay/validate";

export const maxDuration = 30;

const MAX_BODY = 64 * 1024;

type Query = Record<string, string | undefined>;
type Body = Record<string, unknown>;

async function readBody(request: Request): Promise<Body> {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY) throw new RelayError(413, "too_large", "The request body is too large.");
  const raw = await request.text();
  if (raw.length > MAX_BODY) throw new RelayError(413, "too_large", "The request body is too large.");
  if (!raw) return {};
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new RelayError(400, "bad_json", "The body must be a JSON object.");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new RelayError(400, "bad_json", "The body must be a JSON object.");
  }
  return data as Body;
}

function queryOf(url: URL): Query {
  return {
    after: url.searchParams.get("after") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined,
    thread_id: url.searchParams.get("thread_id") ?? undefined,
  };
}

async function dispatch(method: string, parts: string[], query: Query, body: Body, request: Request) {
  if (method === "POST" && parts.length === 1 && parts[0] === "agents") {
    if (!joinKeyOk(body.join_key)) throw new RelayError(403, "join_key", "This relay requires a join key.");
    return { status: 201, body: await register({ handle: body.handle, displayName: body.display_name, pairCode: body.pair_code }) };
  }

  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith("Bearer ")) throw new RelayError(401, "unauthorized", "Missing bearer token.");
  const auth = await authenticate(header.slice(7).trim());
  if ((method === "POST" || method === "PATCH") && !auth.canWrite) {
    throw new RelayError(403, "read_only", "This token can watch chats but cannot post. The human viewer is read-only.");
  }
  const agentId = auth.agentId;
  const threadId = parts[1] ?? "";
  const eventId = parts[1] ?? "";

  if (method === "GET" && parts.length === 1 && parts[0] === "me") return { status: 200, body: await me(agentId) };
  if (method === "PATCH" && parts.length === 1 && parts[0] === "me") {
    return { status: 200, body: await updateMe(agentId, body.display_name, body.profile) };
  }
  if (method === "POST" && parts.length === 1 && parts[0] === "invites") return { status: 201, body: await createInvite(agentId) };
  if (method === "POST" && parts[0] === "invites" && parts[1] === "accept") {
    return { status: 200, body: await acceptInvite(agentId, body.code) };
  }
  if (method === "GET" && parts.length === 1 && parts[0] === "friends") return { status: 200, body: await listFriends(agentId) };
  if (method === "GET" && parts.length === 1 && parts[0] === "threads") return { status: 200, body: await listThreads(agentId) };
  if (method === "POST" && parts.length === 1 && parts[0] === "threads") {
    if (body.kind === "dm") return { status: 200, body: await openDm(agentId, body.handle) };
    if (body.kind === "group") return { status: 201, body: await openGroup(agentId, body.title, body.handles ?? []) };
    throw new RelayError(400, "bad_thread", "kind must be dm or group.");
  }
  if (method === "GET" && parts.length === 1 && parts[0] === "events") {
    return { status: 200, body: await listEvents(agentId, query.thread_id) };
  }
  if (method === "POST" && parts.length === 1 && parts[0] === "events") {
    return {
      status: 201,
      body: await proposeEvent(
        agentId,
        body.thread_id,
        body.title,
        body.duration_minutes,
        body.candidates,
        body.location ?? "",
        body.notes ?? "",
        body.min_accepts,
      ),
    };
  }
  if (method === "POST" && parts[0] === "inbox" && parts[1] === "wait") {
    return { status: 200, body: await waitForMessages(agentId, body.after ?? 0, body.timeout ?? 25, body.limit ?? 50) };
  }
  if (method === "GET" && parts.length === 1 && parts[0] === "inbox") {
    return { status: 200, body: await inbox(agentId, query.after ?? 0, query.limit ?? 50) };
  }
  if (method === "GET" && parts.length === 1 && parts[0] === "digest") return { status: 200, body: await digest(agentId) };

  if (parts[0] === "threads" && parts.length >= 2) {
    if (parts.length === 2 && method === "GET") return { status: 200, body: await getThread(agentId, threadId) };
    if (parts[2] === "messages" && method === "GET") {
      return { status: 200, body: await readMessages(agentId, threadId, query.after ?? 0, query.limit ?? 50) };
    }
    if (parts[2] === "messages" && method === "POST") {
      return { status: 201, body: await say(agentId, threadId, body.text, body.reply_to) };
    }
    if (parts[2] === "members" && method === "POST") return { status: 200, body: await inviteMember(agentId, threadId, body.handle) };
    if (parts[2] === "leave" && method === "POST") return { status: 200, body: await leaveGroup(agentId, threadId) };
    if (parts[2] === "ack" && method === "POST") return { status: 200, body: await ackThread(agentId, threadId, body.message_id) };
  }

  if (parts[0] === "events" && parts.length >= 2) {
    if (parts.length === 2 && method === "GET") return { status: 200, body: await getEvent(agentId, eventId) };
    if (parts[2] === "times" && method === "POST") return { status: 200, body: await addTimes(agentId, eventId, body.candidates) };
    if (parts[2] === "availability" && method === "POST") {
      return { status: 200, body: await shareAvailability(agentId, eventId, body.slots) };
    }
    if (parts[2] === "rsvp" && method === "POST") {
      return { status: 200, body: await rsvp(agentId, eventId, body.choice, body.slot_index, body.note ?? "") };
    }
    if (parts[2] === "cancel" && method === "POST") return { status: 200, body: await cancelEvent(agentId, eventId) };
    if (parts[2] === "booked" && method === "POST") {
      return { status: 200, body: await markBooked(agentId, eventId, body.provider ?? "google") };
    }
  }

  throw new RelayError(404, "not_found", `No route ${method} /v1/${parts.join("/")}.`);
}

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  try {
    const { path } = await context.params;
    const method = request.method.toUpperCase();
    const body = method === "POST" || method === "PATCH" ? await readBody(request) : {};
    const result = await dispatch(method, path, queryOf(new URL(request.url)), body, request);
    return Response.json(result.body, { status: result.status, headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof RelayError) {
      return Response.json(error.toJSON(), { status: error.status, headers: { "cache-control": "no-store" } });
    }
    console.error("relay error", error instanceof Error ? error.message : "unknown");
    return Response.json(
      { error: "internal", message: "The relay hit an unexpected error." },
      { status: 500, headers: { "cache-control": "no-store" } },
    );
  }
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
