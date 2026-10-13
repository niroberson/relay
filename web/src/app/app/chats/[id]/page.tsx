import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";

import { Transcript } from "@/components/transcript";
import { requireHuman } from "@/lib/human";
import { humanThread } from "@/lib/relay/service";

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <ChatBody params={params} />
    </Suspense>
  );
}

async function ChatBody({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const data = await humanThread(await requireHuman(), id);
  return (
    <div className="flex flex-col gap-4">
      <Link href="/app/chats" className="text-sm text-muted-foreground hover:text-foreground">
        All chats
      </Link>
      <Transcript threadId={id} initial={data} />
    </div>
  );
}
