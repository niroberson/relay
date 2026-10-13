"use client";

import { useActionState } from "react";

export function ActionForm({
  action,
  children,
  className,
}: {
  action: (formData: FormData) => Promise<string | null>;
  children: React.ReactNode;
  className?: string;
}) {
  const [message, formAction, pending] = useActionState(async (_state: string | null, formData: FormData) => {
    return action(formData);
  }, null);
  return (
    <form action={formAction} className={className} data-pending={pending ? "true" : "false"}>
      {children}
      {message ? <p className="text-sm text-destructive">{message}</p> : null}
    </form>
  );
}
