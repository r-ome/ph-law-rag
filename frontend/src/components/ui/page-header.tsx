import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

function PageHeader({
  eyebrow,
  title,
  subtitle,
  meta,
  actions,
  className,
}: {
  eyebrow?: string
  title: ReactNode
  subtitle?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("mb-6 flex items-end justify-between gap-5", className)}>
      <div className="min-w-0">
        {eyebrow ? (
          <div className="mb-1.5 text-[11px] font-semibold tracking-[0.18em] text-gold uppercase">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="font-serif text-3xl font-semibold tracking-[-0.015em]">{title}</h1>
        {subtitle ? (
          <p className="mt-1.5 text-[13.5px] text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      {meta || actions ? (
        <div className="flex shrink-0 items-center gap-3">
          {meta ? (
            <div className="text-right text-xs whitespace-nowrap text-faint">{meta}</div>
          ) : null}
          {actions}
        </div>
      ) : null}
    </div>
  )
}

export { PageHeader }
