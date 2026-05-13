import { formatGenerationElapsed, formatHistoryTimestamp } from "../utils/history";

interface GenerationTimeInfoProps {
  startedAt?: string | null;
  completedAt?: string | null;
  elapsedMs?: number | null;
  compact?: boolean;
}

export function GenerationTimeInfo({ startedAt = null, completedAt = null, elapsedMs = null, compact = false }: GenerationTimeInfoProps) {
  const elapsedLabel = formatGenerationElapsed(elapsedMs);
  if (!elapsedLabel && !startedAt && !completedAt) {
    return null;
  }

  return (
    <div className={compact ? "generation-time-info compact" : "generation-time-info"}>
      {elapsedLabel ? (
        <span className="generation-time-pill">
          <strong>生成耗时</strong>
          <span>{elapsedLabel}</span>
        </span>
      ) : null}
      {startedAt ? (
        <span className="generation-time-meta">
          <strong>开始</strong>
          <span>{formatHistoryTimestamp(startedAt)}</span>
        </span>
      ) : null}
      {completedAt ? (
        <span className="generation-time-meta">
          <strong>完成</strong>
          <span>{formatHistoryTimestamp(completedAt)}</span>
        </span>
      ) : null}
    </div>
  );
}
