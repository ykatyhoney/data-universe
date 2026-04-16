import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider",
  {
    variants: {
      variant: {
        default: "border-border bg-secondary text-secondary-foreground",
        ok: "border-ok/40 bg-ok/10 text-ok",
        warn: "border-warn/40 bg-warn/10 text-warn",
        err: "border-err/40 bg-err/10 text-err",
        muted: "border-border bg-transparent text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
