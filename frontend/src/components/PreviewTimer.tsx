import { useEffect, useMemo, useState } from "react";

interface PreviewTimerProps {
  startedAt: string | null;
  running: boolean;
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

export function PreviewTimer({ startedAt, running }: PreviewTimerProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running || !startedAt) {
      return;
    }
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, startedAt]);

  const label = useMemo(() => {
    if (!running || !startedAt) {
      return null;
    }
    const startedMs = Date.parse(startedAt);
    if (!Number.isFinite(startedMs)) {
      return null;
    }
    return formatElapsed(Math.max(0, now - startedMs));
  }, [now, running, startedAt]);

  if (!label) {
    return null;
  }

  return <span className="preview-timer-pill">{label}</span>;
}
