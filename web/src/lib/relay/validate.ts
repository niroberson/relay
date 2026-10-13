import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

import { RelayError } from "./errors";
import type { AvailabilitySlot, TimeWindow } from "./schema";

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PROFILE_LIMITS: Record<string, number> = { timezone: 64, city: 80, notes_for_friends: 500 };

export function hashToken(token: string) {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

export function tokensMatch(stored: string, presented: string) {
  const left = Buffer.from(stored);
  const right = Buffer.from(presented);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function newId(prefix: string, bytes: number) {
  return prefix + randomBytes(bytes).toString("hex");
}

export function newSecret() {
  return randomBytes(32).toString("base64url");
}

export function inviteCode() {
  const bytes = randomBytes(8);
  let raw = "";
  for (let i = 0; i < 8; i += 1) raw += ALPHABET[bytes[i]! % ALPHABET.length];
  return `${raw.slice(0, 4)}-${raw.slice(4)}`;
}

export function nowIso(date = new Date()) {
  const [head, rest] = date.toISOString().split(".");
  const millis = (rest ?? "000Z").replace("Z", "");
  return `${head}.${millis.padEnd(6, "0")}+00:00`;
}

export function plusMs(iso: string, ms: number) {
  return nowIso(new Date(Date.parse(iso) + ms));
}

export function normalizeHandle(value: unknown) {
  if (typeof value !== "string") throw new RelayError(400, "bad_handle", "A handle must be text.");
  const handle = value.trim().toLowerCase();
  if (handle.length < 2 || handle.length > 32 || !handleOk(handle)) {
    throw new RelayError(
      400,
      "bad_handle",
      "A handle is 2–32 characters: a letter, then lowercase letters, numbers, or hyphens, and it cannot end with a hyphen.",
    );
  }
  return handle;
}

function handleOk(handle: string) {
  if (handle[0]! < "a" || handle[0]! > "z" || handle.endsWith("-")) return false;
  return [...handle].every((char) => (char >= "a" && char <= "z") || (char >= "0" && char <= "9") || char === "-");
}

export function cleanDisplay(value: unknown, field: string) {
  if (typeof value !== "string") throw new RelayError(400, "bad_name", `The ${field} must be text.`);
  const text = value.split(/\s+/).filter(Boolean).join(" ");
  if (!text || text.length > 80) throw new RelayError(400, "bad_name", `The ${field} must be 1–80 characters.`);
  return text;
}

export function cleanText(value: unknown, limit: number, field: string) {
  if (typeof value !== "string") throw new RelayError(400, "bad_text", `The ${field} must be text.`);
  const text = value.replaceAll("\u0000", "").trim();
  if (!text) throw new RelayError(400, "bad_text", `The ${field} is empty.`);
  if (text.length > limit) throw new RelayError(400, "bad_text", `The ${field} must be at most ${limit} characters.`);
  return text;
}

export function cleanOptional(value: unknown, limit: number, field: string) {
  if (value == null) return "";
  if (typeof value !== "string") throw new RelayError(400, "bad_text", `The ${field} must be text.`);
  const text = value.replaceAll("\u0000", "").split(/\s+/).filter(Boolean).join(" ");
  if (text.length > limit) throw new RelayError(400, "bad_text", `The ${field} must be at most ${limit} characters.`);
  return text;
}

export function normalizeCode(code: unknown, label = "invite code") {
  if (typeof code !== "string") throw new RelayError(400, "bad_invite", `The ${label} must be text.`);
  const normalized = [...code.toUpperCase()].filter((char) => /[A-Z0-9]/.test(char)).join("");
  if (normalized.length < 6) throw new RelayError(400, "bad_invite", `That ${label} is not complete.`);
  return normalized;
}

type Moment = { instant: Date; offsetMinutes: number };

export function parseTime(value: unknown): Moment {
  if (typeof value !== "string") throw new RelayError(400, "bad_time", "Times must be timezone-aware ISO-8601 strings.");
  const trimmed = value.trim();
  if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(trimmed)) {
    throw new RelayError(400, "bad_time", "Times must include a timezone offset.");
  }
  const text = /[zZ]$/.test(trimmed) ? `${trimmed.slice(0, -1)}+00:00` : trimmed;
  const instant = new Date(text);
  if (Number.isNaN(instant.getTime())) throw new RelayError(400, "bad_time", `Could not parse time ${JSON.stringify(value)}.`);
  const match = text.match(/([+-])(\d{2}):(\d{2})$/);
  const sign = match?.[1] === "-" ? -1 : 1;
  const offsetMinutes = match ? sign * (Number(match[2]) * 60 + Number(match[3])) : 0;
  return { instant, offsetMinutes };
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function wall(moment: Moment) {
  const shifted = new Date(moment.instant.getTime() + moment.offsetMinutes * 60000);
  return {
    y: shifted.getUTCFullYear(),
    m: shifted.getUTCMonth() + 1,
    d: shifted.getUTCDate(),
    hh: shifted.getUTCHours(),
    mm: shifted.getUTCMinutes(),
  };
}

function offsetLabel(minutes: number) {
  const sign = minutes >= 0 ? "+" : "-";
  const abs = Math.abs(minutes);
  return `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

function stamp(moment: Moment) {
  const parts = wall(moment);
  return `${parts.y}-${pad(parts.m)}-${pad(parts.d)} ${pad(parts.hh)}:${pad(parts.mm)}`;
}

export function labelRange(start: Moment, end: Moment) {
  const startOff = offsetLabel(start.offsetMinutes);
  const endOff = offsetLabel(end.offsetMinutes);
  const left = wall(start);
  const right = wall(end);
  const sameDay = left.y === right.y && left.m === right.m && left.d === right.d;
  if (startOff === endOff && sameDay) return `${stamp(start)}–${pad(right.hh)}:${pad(right.mm)} ${startOff}`;
  return `${stamp(start)} ${startOff} – ${stamp(end)} ${endOff}`;
}

function windowFor(start: Moment, end: Moment): TimeWindow {
  if (end.instant <= start.instant) throw new RelayError(400, "bad_time", "A window's end must be after its start.");
  const minutes = (end.instant.getTime() - start.instant.getTime()) / 60000;
  if (minutes < 15 || minutes > 24 * 60) {
    throw new RelayError(400, "bad_time", "A window must last between 15 minutes and 24 hours.");
  }
  return { start: nowIso(start.instant), end: nowIso(end.instant), label: labelRange(start, end) };
}

export function parseCandidates(raw: unknown, durationMinutes: number): TimeWindow[] {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 8) {
    throw new RelayError(400, "bad_times", "Offer between 1 and 8 candidate times.");
  }
  return raw.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new RelayError(400, "bad_times", "Each candidate time must be an object with start and end.");
    }
    const extra = Object.keys(item).filter((key) => key !== "start" && key !== "end");
    if (extra.length) throw new RelayError(400, "bad_times", "A candidate time only has start and end.");
    const record = item as { start?: unknown; end?: unknown };
    const window = windowFor(parseTime(record.start), parseTime(record.end));
    const length = (Date.parse(window.end) - Date.parse(window.start)) / 60000;
    if (Math.abs(length - durationMinutes) > 1) {
      throw new RelayError(400, "bad_times", "Each candidate must be duration_minutes long.");
    }
    return window;
  });
}

export function parseAvailability(raw: unknown): AvailabilitySlot[] {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 12) {
    throw new RelayError(400, "bad_availability", "Share between 1 and 12 availability windows.");
  }
  return raw.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new RelayError(400, "bad_availability", "Each availability window must be an object.");
    }
    const extra = Object.keys(item).filter((key) => !["start", "end", "status"].includes(key));
    if (extra.length) {
      throw new RelayError(
        400,
        "private_fields",
        "Availability windows only allow start, end, and status. Leave calendar titles, guests, and descriptions out.",
      );
    }
    const record = item as { start?: unknown; end?: unknown; status?: unknown };
    if (record.status !== "free" && record.status !== "busy" && record.status !== "maybe") {
      throw new RelayError(400, "bad_availability", "Status must be free, busy, or maybe.");
    }
    return { ...windowFor(parseTime(record.start), parseTime(record.end)), status: record.status };
  });
}

export function cleanProfile(updates: unknown) {
  if (!updates || typeof updates !== "object" || Array.isArray(updates)) {
    throw new RelayError(400, "bad_profile", "Profile must be an object.");
  }
  const record = updates as Record<string, unknown>;
  const extra = Object.keys(record).filter((key) => !(key in PROFILE_LIMITS));
  if (extra.length) {
    throw new RelayError(400, "bad_profile", "Profile only accepts timezone, city, and notes_for_friends.");
  }
  const cleaned: Record<string, string> = {};
  for (const [key, limit] of Object.entries(PROFILE_LIMITS)) {
    if (!(key in record)) continue;
    const value = record[key];
    if (typeof value !== "string") throw new RelayError(400, "bad_profile", `${key} must be text.`);
    const text = value.replaceAll("\u0000", "").split(/\s+/).filter(Boolean).join(" ");
    if (text.length > limit) throw new RelayError(400, "bad_profile", `${key} must be at most ${limit} characters.`);
    if (key === "timezone" && text && ![...text].every((char) => /[A-Za-z0-9_/+-]/.test(char))) {
      throw new RelayError(400, "bad_profile", "Timezone must be an IANA name such as America/Los_Angeles.");
    }
    cleaned[key] = text;
  }
  return cleaned;
}

export function cursor(after: unknown, limit: unknown) {
  const parsedAfter = after == null || after === "" ? 0 : nonNegativeInt(after, "after");
  let parsedLimit = limit == null || limit === "" ? 50 : positiveInt(limit, "limit");
  if (parsedLimit > 200) parsedLimit = 200;
  return { after: parsedAfter, limit: parsedLimit };
}

export function positiveInt(value: unknown, field: string) {
  const number = nonNegativeInt(value, field);
  if (number < 1) throw new RelayError(400, "bad_number", `${field} must be at least 1.`);
  return number;
}

export function nonNegativeInt(value: unknown, field: string) {
  if (typeof value === "boolean" || !["number", "string"].includes(typeof value)) {
    throw new RelayError(400, "bad_number", `${field} must be an integer.`);
  }
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(number)) throw new RelayError(400, "bad_number", `${field} must be an integer.`);
  if (number < 0) throw new RelayError(400, "bad_number", `${field} must be zero or more.`);
  return number;
}

export function durationMinutes(value: unknown) {
  const number = positiveInt(value, "duration_minutes");
  if (number < 15 || number > 24 * 60) {
    throw new RelayError(400, "bad_times", "duration_minutes must be between 15 and 1440.");
  }
  return number;
}

export function olderThan(iso: string | null, seconds: number, now: string) {
  if (!iso) return true;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return true;
  return Date.parse(now) - then >= seconds * 1000;
}

export function pairOrder(left: string, right: string) {
  return left < right ? ([left, right] as const) : ([right, left] as const);
}

export function providerName(value: unknown) {
  const raw = typeof value === "string" && value.trim() ? value : "google";
  return [...raw.toLowerCase()].filter((char) => char >= "a" && char <= "z").join("").slice(0, 32) || "google";
}

export function joinKeyOk(presented: unknown) {
  const expected = process.env.RELAY_JOIN_KEY ?? "";
  if (!expected) return true;
  const got = typeof presented === "string" ? presented : "";
  return tokensMatch(hashToken(expected), hashToken(got));
}

export function pairRequired() {
  return process.env.RELAY_REQUIRE_PAIR === "1";
}
