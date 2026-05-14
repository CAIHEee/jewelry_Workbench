interface GeneratingImagePlaceholderProps {
  percent?: number | null;
  label?: string;
}

export function GeneratingImagePlaceholder({ percent, label = "图片生成中" }: GeneratingImagePlaceholderProps) {
  const safePercent = Math.max(0, Math.min(100, Math.round(percent ?? 18)));

  return (
    <div className="agent-generation-placeholder generation-image-placeholder" aria-label={`${label}，${safePercent}%`} role="img">
      <span />
      <strong>{safePercent}%</strong>
    </div>
  );
}
