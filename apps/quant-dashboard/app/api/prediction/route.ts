import { NextRequest, NextResponse } from "next/server";
import dashboard from "../../../public/data/dashboard.json";

export function GET(request: NextRequest) {
  const rawHorizon = request.nextUrl.searchParams.get("horizon");
  const horizon = Number(rawHorizon);
  if (!Number.isInteger(horizon) || horizon < 1 || horizon > 252) {
    return NextResponse.json(
      { error: "horizon 必须是 1 到 252 之间的整数交易日。" },
      { status: 400 },
    );
  }

  const trainedHorizon = dashboard.configuration.horizon_trading_days;
  if (horizon !== trainedHorizon) {
    return NextResponse.json(
      {
        error: `尚无 ${horizon} 日的独立样本外模型，不能沿用 ${trainedHorizon} 日模型冒充结果。`,
        requested_horizon: horizon,
        available_horizons: [trainedHorizon],
        requires_training: true,
      },
      { status: 404 },
    );
  }

  return NextResponse.json({
    status: "historical_only",
    horizon_trading_days: trainedHorizon,
    signal_date: dashboard.equity_curve.at(-1)?.date,
    prediction_type:
      (dashboard as { score_semantics?: string }).score_semantics ?? "predicted_forward_return",
    price_prediction_available: false,
    accuracy_definition: "样本外预测收益方向与实际收益方向一致的比例",
    metrics: {
      direction_accuracy: dashboard.prediction.direction_accuracy,
      mae: dashboard.prediction.mae,
      rmse: dashboard.prediction.rmse,
      mean_rank_ic: dashboard.prediction.mean_daily_rank_ic,
      rank_ic_ir: dashboard.prediction.rank_ic_ir,
      observations: dashboard.prediction.observations,
    },
    historical_selections: {
      top_10_long: dashboard.latest_holdings
        .filter((holding) => holding.side === "long")
        .map(({ instrument, score, weight }) => ({ instrument, score, weight })),
      bottom_10_short: dashboard.latest_holdings
        .filter((holding) => holding.side === "short")
        .map(({ instrument, score, weight }) => ({ instrument, score, weight })),
    },
    warning:
      "这里只提供已完成并含事后标签的历史研究名单。当前有效信号不可用；这些结果不是实时交易指令。",
  });
}
