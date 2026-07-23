import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

type Tint = "primary" | "gold" | "danger" | "warn" | "violet" | "muted"

const tagTintClass: Record<Tint, string> = {
  primary: "bg-primary-bg text-primary",
  gold: "bg-gold-bg text-gold",
  danger: "bg-danger-bg text-danger",
  warn: "bg-warn-bg text-warn",
  violet: "bg-violet-bg text-violet",
  muted: "bg-muted text-muted-foreground",
}

function StatTile({
  label,
  value,
  note,
  tag,
  tagTint = "primary",
  className,
}: {
  label: ReactNode
  value: ReactNode
  note?: ReactNode
  tag?: ReactNode
  tagTint?: Tint
  className?: string
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card p-4 shadow-[var(--shadow)]",
        className
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] font-semibold tracking-[0.04em] text-muted-foreground uppercase">
          {label}
        </span>
        {tag ? (
          <span
            className={cn(
              "rounded-[5px] px-1.5 py-0.5 text-[10.5px] font-semibold",
              tagTintClass[tagTint]
            )}
          >
            {tag}
          </span>
        ) : null}
      </div>
      <div className="mt-2.5 font-serif text-[33px] leading-[1.05] font-semibold tracking-[-0.02em]">
        {value}
      </div>
      {note ? <div className="mt-0.5 text-[11.5px] text-faint">{note}</div> : null}
    </div>
  )
}

export { StatTile }
