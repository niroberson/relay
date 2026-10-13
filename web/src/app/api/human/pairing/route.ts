import { requireHuman } from "@/lib/human";
import { RelayError } from "@/lib/relay/errors";
import { createPairing } from "@/lib/relay/service";

export async function POST(request: Request) {
  try {
    const userId = await requireHuman();
    const body = (await request.json()) as { handle?: unknown; display_name?: unknown };
    const created = await createPairing(userId, body.handle, body.display_name);
    return Response.json(created, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof RelayError) return Response.json(error.toJSON(), { status: error.status });
    console.error("pairing error", error instanceof Error ? error.message : "unknown");
    return Response.json({ error: "internal", message: "Could not create a pairing code." }, { status: 500 });
  }
}
