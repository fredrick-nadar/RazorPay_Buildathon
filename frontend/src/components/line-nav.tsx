/**
 * This component is inspired by Devouring Details, Skiper UI, and @ncdai/line-nav.
 */

"use client";

import { memo, useEffect, useRef } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

const lineVariants = {
  normal: { width: 24 },
  active: { width: 40 },
  hover: { width: 40 },
};

export type LineNavItem = {
  title: string;
  href: string;
  id?: string;
  timestamp?: string;
  subtitle?: string;
};

export type LineNavProps = {
  className?: string;
  /** List of navigation items */
  items: LineNavItem[];
  /** Href of the active item. */
  activeHref?: string;
  /** Scroll the active item into view on mount. */
  scrollActiveIntoView?: boolean;
  /** Whether to show title only on hover */
  showTitleOnlyOnHover?: boolean;
  /** Called when an item is clicked. */
  onItemClick?: (
    item: LineNavItem,
    event: React.MouseEvent<HTMLAnchorElement>
  ) => void;
};

export function LineNav({
  className,
  items,
  activeHref,
  scrollActiveIntoView = false,
  showTitleOnlyOnHover = false,
  onItemClick,
}: LineNavProps) {
  const activeItemRef = useRef<HTMLAnchorElement | null>(null);

  useEffect(() => {
    if (scrollActiveIntoView) {
      activeItemRef.current?.scrollIntoView({ block: "center" });
    }
  }, [scrollActiveIntoView]);

  return (
    <nav
      className={cn("flex flex-col gap-2 py-3 select-none", className)}
      style={
        {
          "--line-nav-width": `${lineVariants.normal.width}px`,
        } as React.CSSProperties
      }
    >
      {items.map((item, index) => {
        const isActive = item.href === activeHref;

        return (
          <LineNavItem
            key={item.href || index}
            ref={isActive ? activeItemRef : undefined}
            title={item.title}
            href={item.href}
            timestamp={item.timestamp}
            subtitle={item.subtitle}
            active={isActive}
            isLast={index === items.length - 1}
            showTitleOnlyOnHover={showTitleOnlyOnHover}
            onClick={
              onItemClick ? (event) => onItemClick(item, event) : undefined
            }
          />
        );
      })}
    </nav>
  );
}

interface LineNavItemInternalProps {
  ref?: React.Ref<HTMLAnchorElement>;
  title: string;
  href: string;
  timestamp?: string;
  subtitle?: string;
  active?: boolean;
  isLast?: boolean;
  showTitleOnlyOnHover?: boolean;
  onClick?: React.MouseEventHandler<HTMLAnchorElement>;
}

const LineNavItem = memo(function LineNavItem({
  ref,
  title,
  href,
  timestamp,
  subtitle,
  active = false,
  isLast = false,
  showTitleOnlyOnHover = false,
  onClick,
}: LineNavItemInternalProps) {
  return (
    <>
      <motion.a
        ref={ref}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group relative flex h-px items-center gap-3 after:absolute after:top-1/2 after:left-0 after:size-full after:-translate-y-1/2 after:p-3.5 cursor-pointer",
          showTitleOnlyOnHover && "w-fit"
        )}
        href={href}
        initial={false}
        animate={active ? "active" : "normal"}
        whileHover="hover"
        onClick={onClick}
      >
        <motion.span
          className={cn(
            "block h-px shrink-0 transition-colors duration-150 ease-out",
            active
              ? "bg-slate-900"
              : "bg-slate-300/80 group-hover:bg-slate-900"
          )}
          variants={lineVariants}
          transition={{ type: "spring", stiffness: 220, damping: 22 }}
        />

        {showTitleOnlyOnHover ? (
          <div className="absolute left-full pl-3 top-1/2 -translate-y-1/2 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-all duration-150 ease-out z-50 whitespace-nowrap">
            <div className="rounded-xl border border-slate-800 bg-[#16171d]/95 text-white px-3 py-2 shadow-2xl backdrop-blur-xl max-w-xs flex flex-col gap-0.5">
              <span className="text-xs font-semibold tracking-tight text-white leading-snug truncate max-w-[220px]">
                {title}
              </span>
              {subtitle && (
                <span className="text-[11px] text-slate-300 font-normal leading-relaxed line-clamp-2 max-w-[220px] whitespace-normal">
                  {subtitle}
                </span>
              )}
              {timestamp && (
                <span className="text-[10px] text-slate-400 font-mono mt-0.5">
                  {timestamp}
                </span>
              )}
            </div>
          </div>
        ) : (
          <span className="text-sm whitespace-nowrap text-slate-500 transition-colors ease-out group-hover:text-slate-900 group-aria-[current=page]:text-slate-900 group-aria-[current=page]:font-medium">
            {title}
          </span>
        )}
      </motion.a>

      {!isLast && (
        <>
          <span className="block h-px w-(--line-nav-width) bg-slate-200/70" />
          <span className="block h-px w-(--line-nav-width) bg-slate-200/70" />
        </>
      )}
    </>
  );
});

export default LineNav;
