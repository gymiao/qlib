import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

import { canonicalHash } from "../../../../../lib/canonical";

export const dynamic = "force-dynamic";

function unavailable(reason: string, status: number, extra: Record<string, unknown> = {}) {
  return NextResponse.json(
    {
      schema_version: "dashboard_signal.v1",
      kind: "signal_batch",
      status: "unavailable",
      reason_codes: [reason],
      read_only: true,
      ...extra,
    },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export async function GET(request: NextRequest) {
  const snapshotPath = process.env.QLIB_CURRENT_SIGNAL_PATH
    ?? path.join(process.cwd(), "public", "data", "signal-current.json");
  try {
    const parsed: unknown = JSON.parse(await readFile(snapshotPath, "utf8"));
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return unavailable("CURRENT_SIGNAL_INVALID", 503);
    }
    const payload = parsed as Record<string, unknown>;
    const { content_sha256: contentHash, ...identity } = payload;
    if (
      payload.schema_version !== "dashboard_signal.v1"
      || payload.kind !== "signal_batch"
      || payload.status !== "valid"
      || payload.read_only !== true
      || typeof contentHash !== "string"
      || !/^[0-9a-f]{64}$/.test(contentHash)
      || canonicalHash(identity) !== contentHash
    ) {
      return unavailable("CURRENT_SIGNAL_INTEGRITY_FAILED", 503);
    }
    const signal = payload.signal as Record<string, unknown> | undefined;
    const requestedStrategy = request.nextUrl.searchParams.get("strategy_id");
    const rawHorizon = request.nextUrl.searchParams.get("horizon");
    if (requestedStrategy && signal?.strategy_id !== requestedStrategy) {
      return unavailable("STRATEGY_NOT_AVAILABLE", 404, {
        requested_strategy_id: requestedStrategy,
        available_strategy_ids: signal?.strategy_id ? [signal.strategy_id] : [],
      });
    }
    if (rawHorizon !== null) {
      const horizon = Number(rawHorizon);
      if (!Number.isInteger(horizon) || horizon < 1 || horizon > 252) {
        return unavailable("INVALID_HORIZON", 400);
      }
      if (signal?.horizon !== horizon) {
        return unavailable("HORIZON_NOT_AVAILABLE", 404, {
          requested_horizon: horizon,
          available_horizons: typeof signal?.horizon === "number" ? [signal.horizon] : [],
        });
      }
    }
    const asOf = new Date();
    const decision = new Date(String(signal?.decision_time));
    const validUntil = new Date(String(signal?.valid_until));
    if (
      Number.isNaN(decision.valueOf())
      || Number.isNaN(validUntil.valueOf())
      || !(decision <= asOf && asOf < validUntil)
    ) {
      return unavailable("CURRENT_SIGNAL_STALE", 410);
    }
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return unavailable(
      code === "ENOENT" ? "CURRENT_SIGNAL_NOT_PUBLISHED" : "CURRENT_SIGNAL_INVALID",
      code === "ENOENT" ? 404 : 503,
    );
  }
}
