"use server";

import { revalidatePath } from "next/cache";

import { requireHuman } from "./human";
import { RelayError } from "./relay/errors";
import {
  approveConnection,
  blockHandle,
  declineConnection,
  removeFriend,
  setAutoApprove,
  setPaused,
  unblockHandle,
} from "./relay/service";

async function run(fn: () => Promise<unknown>) {
  try {
    await fn();
  } catch (error) {
    if (error instanceof RelayError) return error.message;
    throw error;
  }
  revalidatePath("/app", "layout");
  return null;
}

export async function approveAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => approveConnection(userId, formData.get("handle")));
}

export async function declineAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => declineConnection(userId, formData.get("handle")));
}

export async function removeAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => removeFriend(userId, formData.get("handle")));
}

export async function blockAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => blockHandle(userId, formData.get("handle")));
}

export async function unblockAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => unblockHandle(userId, formData.get("handle")));
}

export async function pauseAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => setPaused(userId, formData.get("paused") === "1"));
}

export async function autoApproveAction(formData: FormData) {
  const userId = await requireHuman();
  return run(() => setAutoApprove(userId, formData.get("auto_approve") === "1"));
}
