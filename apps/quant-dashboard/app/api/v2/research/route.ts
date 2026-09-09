import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export async function GET() {
  const reportPath = path.join(process.cwd(), "public", "data", "research-v2.json");
  try {
    const payload = JSON.parse(await readFile(reportPath, "utf8"));
    if (payload.kind !== "research_report" || payload.status !== "complete") {
      return NextResponse.json(
        { kind: "research_report", status: "unavailable", reason_codes: ["REPORT_NOT_COMPLETE"] },
        { status: 503 },
      );
    }
    return NextResponse.json(payload);
  } catch {
    return NextResponse.json(
      { kind: "research_report", status: "unavailable", reason_codes: ["REPORT_NOT_PUBLISHED"] },
      { status: 404 },
    );
  }
}
