import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { redirect } from "next/navigation";

import { ClerkMenu } from "@/components/clerk-menu";
import { Badge } from "@/components/ui/badge";
import { clerkConfigured, currentHumanId } from "@/lib/human";

const links = [
  { href: "/app/chats", label: "Chats" },
  { href: "/app/people", label: "People" },
  { href: "/app/agent", label: "Agent" },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-6">
            <Link href="/app" className="text-sm font-medium">
              Relay
            </Link>
            <nav className="flex items-center gap-4 text-sm text-muted-foreground">
              {links.map((link) => (
                <Link key={link.href} href={link.href} className="hover:text-foreground">
                  {link.label}
                </Link>
              ))}
            </nav>
          </div>
          {clerkConfigured() ? (
            <Suspense fallback={null}>
              <ClerkMenu />
            </Suspense>
          ) : (
            <Badge variant="outline">Local preview</Badge>
          )}
        </div>
      </header>
      <Suspense fallback={<p className="mx-auto w-full max-w-5xl px-6 py-8 text-sm text-muted-foreground">Loading…</p>}>
        <Gate>{children}</Gate>
      </Suspense>
    </div>
  );
}

async function Gate({ children }: { children: React.ReactNode }) {
  await connection();
  const userId = await currentHumanId();
  if (!userId) redirect("/sign-in");
  return <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-6 py-8">{children}</div>;
}
