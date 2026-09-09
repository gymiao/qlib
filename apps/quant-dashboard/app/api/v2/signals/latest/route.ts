import { NextResponse } from "next/server";

export function GET() {
  return NextResponse.json(
    {
      schema_version: "signal_batch.v1",
      kind: "signal_batch",
      status: "unavailable",
      reason_codes: ["CURRENT_SIGNAL_NOT_PUBLISHED"],
    },
    { status: 404 },
  );
}
