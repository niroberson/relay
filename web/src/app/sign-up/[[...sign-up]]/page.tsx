import { SignUp } from "@clerk/nextjs";
import { Suspense } from "react";

import { clerkConfigured } from "@/lib/human";

export default function SignUpPage() {
  if (!clerkConfigured()) {
    return (
      <main className="mx-auto flex min-h-full max-w-lg items-center px-6 py-16 text-sm text-muted-foreground">
        Sign-up opens after Clerk is connected.
      </main>
    );
  }
  return (
    <main className="flex min-h-full items-center justify-center px-6 py-16">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <SignUp />
      </Suspense>
    </main>
  );
}
