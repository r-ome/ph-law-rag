import type { VariantProps } from "class-variance-authority"
import { badgeVariants } from "@/components/ui/badge"

export type BadgeTone = VariantProps<typeof badgeVariants>["variant"]

export function toneVariant(status?: string | null): BadgeTone {
  switch (status) {
    case "ok":
    case "completed":
      return "tint-primary"
    case "failed":
    case "degraded":
      return "danger"
    case "partial":
    case "running":
      return "warn"
    default:
      return "secondary"
  }
}

export function docStatusTone(status?: string | null): BadgeTone {
  switch (status) {
    case "operative":
      return "tint-primary"
    case "amended":
      return "warn"
    case "superseded":
    case "repealed":
      return "secondary"
    case "unknown":
      return "outline"
    default:
      return "secondary"
  }
}
