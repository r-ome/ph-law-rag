import * as React from "react"
import { cn } from "@/lib/utils"

function Panel({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow)]",
        className
      )}
      {...props}
    />
  )
}

function PanelHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-border px-[18px] py-3.5",
        className
      )}
      {...props}
    />
  )
}

function PanelTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2 className={cn("font-serif text-base font-semibold", className)} {...props} />
  )
}

function PanelMeta({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span className={cn("font-mono text-[11.5px] text-faint", className)} {...props} />
  )
}

function PanelBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-[18px] py-3.5", className)} {...props} />
}

export { Panel, PanelHeader, PanelTitle, PanelMeta, PanelBody }
