export function GET() {
  return Response.json({ ok: true, service: "relay" }, { headers: { "cache-control": "no-store" } });
}
