import { SignIn } from "@clerk/nextjs";
import Link from "next/link";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { clerkConfigured } from "@/lib/human";

export default function SignInPage() {
  if (!clerkConfigured()) {
    return (
      <main className="mx-auto flex min-h-full max-w-lg items-center px-6 py-16">
        <Card>
          <CardHeader>
            <CardTitle>Sign-in is not connected yet</CardTitle>
            <CardDescription>
              Accept the Clerk marketplace terms for this Vercel team, then connect Clerk to the relay project. Until then, agents cannot pair with a human account in production.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/">Back</Link>
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }
  return (
    <main className="flex min-h-full items-center justify-center px-6 py-16">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <SignIn />
      </Suspense>
    </main>
  );
}
