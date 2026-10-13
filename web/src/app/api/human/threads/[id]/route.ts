import { requireHuman } from "@/lib/human";
import { RelayError } from "@/lib/relay/errors";
import { humanThread } from "@/lib/relay/service";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  try {
    const userId = await requireHuman();
    const { id } = await context.params;
    return Response.json(await humanThread(userId, id), { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof RelayError) return Response.json(error.toJSON(), { status: error.status });
    console.error("thread error", error instanceof Error ? error.message : "unknown");
    return Response.json({ error: "internal", message: "Could not load that chat." }, { status: 500 });
  }
}
