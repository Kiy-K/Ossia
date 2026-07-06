/**
 * TooltipIconButton — icon button with a hover tooltip.
 *
 * Mirrors the assistant-ui ChatGPT clone's ``TooltipIconButton``: wraps a
 * trigger button with a tooltip label. Uses a simple CSS-based tooltip
 * (``::after`` pseudo-element on hover) so no third-party tooltip library
 * is needed.
 */

"use client";

import { type ComponentPropsWithoutRef, forwardRef } from "react";

export interface TooltipIconButtonProps
  extends ComponentPropsWithoutRef<"button"> {
  /** Tooltip text shown on hover. */
  tooltip: string;
}

export const TooltipIconButton = forwardRef<
  HTMLButtonElement,
  TooltipIconButtonProps
>(({ tooltip, className = "", children, ...props }, ref) => {
  return (
    <button
      ref={ref}
      type="button"
      className={`group relative flex items-center justify-center w-9 h-9 rounded-full text-[#5d5d5d] hover:bg-black/7 dark:text-[#cdcdcd] dark:hover:bg-white/15 transition-colors duration-150 outline-none focus-visible:ring-2 focus-visible:ring-[#0d0d0d]/30 dark:focus-visible:ring-white/30 ${className}`}
      {...props}
    >
      {children}
      {/* Tooltip */}
      <span className="pointer-events-none absolute -top-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-[#0d0d0d] dark:bg-[#ececec] px-2 py-1 text-[11px] font-medium text-white dark:text-[#0d0d0d] opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-50 shadow-md">
        {tooltip}
      </span>
    </button>
  );
});

TooltipIconButton.displayName = "TooltipIconButton";
