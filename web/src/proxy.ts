import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

const PUBLIC_PREFIXES = ["/sign-in", "/sign-up", "/v1", "/health", "/api/human"];

function isPublic(request: NextRequest) {
  const path = request.nextUrl.pathname;
  if (path === "/") return true;
  return PUBLIC_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function clerkReady() {
  return Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
}

function devBypass() {
  return process.env.NODE_ENV === "development" && process.env.VERCEL !== "1" && Boolean(process.env.RELAY_DEV_USER);
}

export async function proxy(request: NextRequest, event: NextFetchEvent) {
  const path = request.nextUrl.pathname;
  if (path === "/health" || path.startsWith("/v1")) return NextResponse.next();
  if (!clerkReady()) {
    if (devBypass() || isPublic(request)) return NextResponse.next();
    return NextResponse.redirect(new URL("/sign-in", request.url));
  }
  if (isPublic(request) && !path.startsWith("/api/human")) return NextResponse.next();
  const { clerkMiddleware } = await import("@clerk/nextjs/server");
  const handler = clerkMiddleware(async (auth) => {
    await auth.protect();
  });
  return handler(request, event);
}

export const config = {
  matcher: ["/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)"],
};
