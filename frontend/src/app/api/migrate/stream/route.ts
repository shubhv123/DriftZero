import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Validate frontend caller credential against DRIFTZERO_UI_TOKEN
function verifyUiAuth(req: NextRequest): NextResponse | null {
  const expectedToken = (process.env.DRIFTZERO_UI_TOKEN || "").trim();

  // Refuse to proxy at all if DRIFTZERO_UI_TOKEN is unset
  if (!expectedToken) {
    return NextResponse.json(
      { error: "Unauthorized: DRIFTZERO_UI_TOKEN is not configured on the frontend server" },
      { status: 401 }
    );
  }

  // Compare with the x-driftzero-ui header or an httpOnly cookie
  const headerToken = (req.headers.get("x-driftzero-ui") || "").trim();
  const cookieToken = (
    req.cookies.get("driftzero_ui_token")?.value ||
    req.cookies.get("x-driftzero-ui")?.value ||
    ""
  ).trim();

  const isHeaderValid = Boolean(headerToken && headerToken === expectedToken);
  const isCookieValid = Boolean(cookieToken && cookieToken === expectedToken);

  if (!isHeaderValid && !isCookieValid) {
    return NextResponse.json(
      { error: "Unauthorized: Invalid or missing UI token (x-driftzero-ui or driftzero_ui_token cookie)" },
      { status: 401 }
    );
  }

  return null;
}

// The right-most X-Forwarded-For hop is the one appended by the trusted proxy in front of
// Next.js; earlier hops are client-controlled and must not be used for rate limiting.
function realPeer(req: NextRequest): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) {
    const hop = xff.split(",").pop()?.trim();
    if (hop) return hop;
  }
  return req.headers.get("x-real-ip")?.trim() || "127.0.0.1";
}

export async function POST(req: NextRequest) {
  // Enforce caller credential before building headers or proxying request
  const authErr = verifyUiAuth(req);
  if (authErr) {
    return authErr;
  }

  const backendUrl =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "http://127.0.0.1:8000";

  const apiKey = (process.env.DRIFTZERO_API_KEY || "").trim();

  try {
    const body = await req.json();

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
    };

    const ghToken = req.headers.get("x-github-token");
    if (ghToken) {
      headers["X-GitHub-Token"] = ghToken;
    }
    const authHeader = req.headers.get("authorization");
    if (authHeader) {
      headers["Authorization"] = authHeader;
    }

    // Forward client IP for rate limiting
    headers["X-Forwarded-For"] = realPeer(req);

    const backendRes = await fetch(`${backendUrl}/api/migrate/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (!backendRes.ok) {
      const errText = await backendRes.text();
      return new NextResponse(errText, {
        status: backendRes.status,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response(backendRes.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  } catch (error: any) {
    return NextResponse.json(
      { error: error?.message || "Failed to proxy migration stream to backend" },
      { status: 500 }
    );
  }
}

export async function GET(req: NextRequest) {
  // Enforce caller credential before building headers or proxying request
  const authErr = verifyUiAuth(req);
  if (authErr) {
    return authErr;
  }

  const backendUrl =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "http://127.0.0.1:8000";

  const apiKey = (process.env.DRIFTZERO_API_KEY || "").trim();

  try {
    const url = new URL(req.url);
    const searchParams = url.searchParams.toString();

    const headers: Record<string, string> = {
      "X-API-Key": apiKey,
    };

    const ghToken = req.headers.get("x-github-token");
    if (ghToken) {
      headers["X-GitHub-Token"] = ghToken;
    }
    const authHeader = req.headers.get("authorization");
    if (authHeader) {
      headers["Authorization"] = authHeader;
    }

    headers["X-Forwarded-For"] = realPeer(req);

    const backendRes = await fetch(
      `${backendUrl}/api/migrate/stream?${searchParams}`,
      {
        method: "GET",
        headers,
      }
    );

    if (!backendRes.ok) {
      const errText = await backendRes.text();
      return new NextResponse(errText, {
        status: backendRes.status,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response(backendRes.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  } catch (error: any) {
    return NextResponse.json(
      { error: error?.message || "Failed to proxy migration stream to backend" },
      { status: 500 }
    );
  }
}
