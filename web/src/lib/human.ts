import { auth } from "@clerk/nextjs/server";

import { RelayError } from "./relay/errors";

export function clerkConfigured() {
  return Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
}

function devBypass() {
  return process.env.NODE_ENV === "development" && process.env.VERCEL !== "1" && Boolean(process.env.RELAY_DEV_USER);
}

export async function currentHumanId() {
  if (clerkConfigured()) {
    const session = await auth();
    return session.userId;
  }
  if (devBypass()) return process.env.RELAY_DEV_USER ?? null;
  return null;
}

export async function requireHuman() {
  const userId = await currentHumanId();
  if (!userId) throw new RelayError(401, "unauthorized", "Sign in to view your agent's chats.");
  return userId;
}
