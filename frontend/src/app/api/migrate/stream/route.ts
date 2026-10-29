import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
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
    const forwardedFor =
      req.headers.get("x-forwarded-for") ||
      req.headers.get("x-real-ip") ||
      "127.0.0.1";
    headers["X-Forwarded-For"] = forwardedFor;

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

    const forwardedFor =
      req.headers.get("x-forwarded-for") ||
      req.headers.get("x-real-ip") ||
      "127.0.0.1";
    headers["X-Forwarded-For"] = forwardedFor;

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

