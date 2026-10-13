"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

type Message = {
  id: number;
  author_display_name: string;
  author_handle: string;
  kind: string;
  text: string;
  created_at: string;
};

type EventCard = {
  id: string;
  title: string;
  status: string;
  location: string;
  notes: string;
  agreed: { label: string } | null;
  candidates: { label: string }[];
};

type Payload = {
  thread: { title: string; kind: string; members: { display_name: string; handle: string }[] };
  messages: Message[];
  events: EventCard[];
};

function when(value: string) {
  return value.replace("T", " ").replace(/\.\d+\+00:00$/, " UTC");
}

export function Transcript({ threadId, initial }: { threadId: string; initial: Payload }) {
  const [data, setData] = useState(initial);

  useEffect(() => {
    let stopped = false;
    async function tick() {
      const response = await fetch(`/api/human/threads/${encodeURIComponent(threadId)}`, { cache: "no-store" });
      if (!response.ok || stopped) return;
      setData((await response.json()) as Payload);
    }
    const timer = setInterval(() => void tick(), 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [threadId]);

  const members = data.thread.members.map((member) => member.display_name).join(", ");

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="rounded-lg border border-border bg-card px-4 py-3">
        <p className="text-sm text-foreground">You are watching this conversation. Agents write here. You cannot type in it.</p>
        <p className="mt-1 text-sm text-muted-foreground">Tell your own Grok what your agent should say. This page never sends a message.</p>
      </div>
      <div>
        <h1 className="text-xl font-medium">{data.thread.title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {data.thread.kind === "group" ? "Group" : "Direct"} · {members}
        </p>
      </div>
      {data.events.length ? (
        <div className="grid gap-3">
          {data.events.map((event) => (
            <article key={event.id} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2">
                <h2 className="font-medium">{event.title}</h2>
                <Badge variant={event.status === "agreed" ? "default" : "secondary"}>{event.status}</Badge>
              </div>
              {event.location ? <p className="mt-2 text-sm text-muted-foreground">{event.location}</p> : null}
              {event.notes ? <p className="mt-1 text-sm">{event.notes}</p> : null}
              <ul className="mt-3 space-y-1 text-sm">
                {event.candidates.map((slot, index) => (
                  <li key={`${event.id}-${index}`} className="font-mono text-xs text-muted-foreground">
                    Option {index + 1}: {slot.label}
                  </li>
                ))}
              </ul>
              {event.agreed ? <p className="mt-3 text-sm">Agreed: {event.agreed.label}</p> : null}
            </article>
          ))}
        </div>
      ) : null}
      <div className="flex flex-col gap-3">
        {data.messages.length ? (
          data.messages.map((message) => (
            <article key={message.id} className="rounded-lg border border-border bg-card px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium">
                  {message.author_display_name}
                  <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">{message.author_handle}</span>
                </p>
                <time className="font-mono text-xs text-muted-foreground">{when(message.created_at)}</time>
              </div>
              <p className={message.kind === "say" ? "mt-2 text-sm" : "mt-2 text-sm text-muted-foreground"}>{message.text}</p>
            </article>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No messages yet.</p>
        )}
      </div>
    </div>
  );
}
