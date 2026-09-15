import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

import { canonicalHash } from "../../../../../lib/canonical";

export const dynamic = "force-dynamic";

function unavailable(reason: string, status: number) {
  return NextResponse.json(
    {
      schema_version: "operations_snapshot.v1",
      kind: "account_snapshot",
      status: "unavailable",
      reason_codes: [reason],
      read_only: true,
    },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export async function GET() {
  const snapshotPath = process.env.QLIB_ACCOUNT_SNAPSHOT_PATH
    ?? path.join(process.cwd(), "public", "data", "account-current.json");
  try {
    const parsed: unknown = JSON.parse(await readFile(snapshotPath, "utf8"));
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return unavailable("ACCOUNT_SNAPSHOT_INVALID", 503);
    }
    const payload = parsed as Record<string, unknown>;
    const { content_sha256: contentHash, ...identity } = payload;
    if (
      payload.schema_version !== "operations_snapshot.v1"
      || payload.kind !== "account_snapshot"
      || payload.status !== "valid"
      || payload.read_only !== true
      || typeof contentHash !== "string"
      || !/^[0-9a-f]{64}$/.test(contentHash)
      || canonicalHash(identity) !== contentHash
    ) {
      return unavailable("ACCOUNT_SNAPSHOT_INTEGRITY_FAILED", 503);
    }
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return unavailable(
      code === "ENOENT" ? "ACCOUNT_SNAPSHOT_NOT_PUBLISHED" : "ACCOUNT_SNAPSHOT_INVALID",
      code === "ENOENT" ? 404 : 503,
    );
  }
}
