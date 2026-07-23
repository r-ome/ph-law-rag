import { cn } from "@/lib/utils"

function ProgressBar({
  value,
  className,
}: {
  value: number // 0–100
  className?: string
}) {
  const pct = Math.max(0, Math.min(100, value))
  return (
    <div className={cn("h-[7px] overflow-hidden rounded-[4px] bg-muted", className)}>
      <div
        className="h-full rounded-[4px] bg-gradient-to-r from-primary to-[oklch(0.55_0.1_150)]"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

export { ProgressBar }
