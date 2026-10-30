import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const token = (body.token || "").trim();
    const expectedToken = (process.env.DRIFTZERO_UI_TOKEN || "").trim();

    if (!expectedToken) {
      return NextResponse.json(
        { error: "DRIFTZERO_UI_TOKEN is not configured on the server" },
        { status: 500 }
      );
    }

    if (!token || token !== expectedToken) {
      return NextResponse.json(
        { error: "Invalid UI authentication token" },
        { status: 401 }
      );
    }

    const response = NextResponse.json({
      success: true,
      message: "Authenticated successfully",
    });

    // Store token in httpOnly cookie
    response.cookies.set("driftzero_ui_token", token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 30, // 30 days
    });

    return response;
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Failed to authenticate" },
      { status: 500 }
    );
  }
}

export async function GET(req: NextRequest) {
  const expectedToken = (process.env.DRIFTZERO_UI_TOKEN || "").trim();
  if (!expectedToken) {
    return NextResponse.json({ authenticated: false, configured: false });
  }

  const cookieToken = (
    req.cookies.get("driftzero_ui_token")?.value ||
    req.cookies.get("x-driftzero-ui")?.value ||
    ""
  ).trim();
  const headerToken = (req.headers.get("x-driftzero-ui") || "").trim();

  const authenticated = Boolean(
    (cookieToken && cookieToken === expectedToken) ||
    (headerToken && headerToken === expectedToken)
  );

  return NextResponse.json({ authenticated, configured: true });
}

export async function DELETE() {
  const response = NextResponse.json({ success: true, message: "Logged out" });
  response.cookies.delete("driftzero_ui_token");
  return response;
}
