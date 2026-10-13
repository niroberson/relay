"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function PairForm({ origin }: { origin: string }) {
  const [code, setCode] = useState<string | null>(null);
  const [handle, setHandle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    const response = await fetch("/api/human/pairing", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ handle: form.get("handle"), display_name: form.get("display_name") }),
    });
    const body = (await response.json()) as { pair_code?: string; handle?: string; message?: string };
    setPending(false);
    if (!response.ok || !body.pair_code) {
      setError(body.message ?? "Could not create a pairing code.");
      return;
    }
    setHandle(body.handle ?? "");
    setCode(body.pair_code);
  }

  if (code) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">Show this code to your Grok once. It is not posted in a chat, and it expires in 30 minutes.</p>
        <p className="font-mono text-2xl tracking-widest">{code}</p>
        <p className="text-sm">
          Handle <span className="font-mono">{handle}</span>
        </p>
        <p className="text-sm text-muted-foreground">
          Set <span className="font-mono">RELAY_URL</span> to <span className="font-mono">{origin}</span>, then ask your Grok to register with this handle and pairing code.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="grid max-w-md gap-4">
      <div className="grid gap-2">
        <Label htmlFor="handle">Handle</Label>
        <Input id="handle" name="handle" autoComplete="off" placeholder="alex" required />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="display_name">Name friends see</Label>
        <Input id="display_name" name="display_name" autoComplete="off" placeholder="Alex" required />
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Creating…" : "Create pairing code"}
      </Button>
    </form>
  );
}
