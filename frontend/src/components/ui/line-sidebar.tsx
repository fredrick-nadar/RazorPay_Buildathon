"use client";

import React, { useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import "./LineSidebar.css";

export interface TurnItem {
  id: string;
  query: string;
  responseSnippet?: string;
  timestamp?: string;
  index: number;
}

interface LineSidebarProps {
  turns: TurnItem[];
  onTurnClick?: (turn: TurnItem) => void;
  className?: string;
}

export function LineSidebar({ turns, onTurnClick, className = "" }: LineSidebarProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [popoverTop, setPopoverTop] = useState<number>(0);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tickRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Dense tick count for the Codex minimap rail (e.g., minimum 28 ticks or scaled by turns)
  const totalTicks = Math.max(28, turns.length * 5);

  // Map each turn to a normalized position along the rail
  const turnMap = React.useMemo(() => {
    const map = new Map<number, TurnItem>();
    if (turns.length === 0) return map;
    if (turns.length === 1) {
      map.set(Math.floor(totalTicks / 2), turns[0]!);
      return map;
    }
    turns.forEach((turn, idx) => {
      const tickPos = Math.round((idx / (turns.length - 1)) * (totalTicks - 1));
      map.set(tickPos, turn);
    });
    return map;
  }, [turns, totalTicks]);

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const container = containerRef.current;
      if (!container || turns.length === 0) return;
      const rect = container.getBoundingClientRect();
      const relativeY = e.clientY - rect.top;
      const fraction = Math.max(0, Math.min(1, relativeY / rect.height));

      // Find closest turn
      const closestTurnIdx = Math.round(fraction * (turns.length - 1));
      setHoveredIndex(closestTurnIdx);

      // Align popover with cursor position (clamped inside container)
      setPopoverTop(Math.max(24, Math.min(rect.height - 24, relativeY)));
    },
    [turns],
  );

  const handlePointerLeave = useCallback(() => {
    setHoveredIndex(null);
  }, []);

  const handleClick = useCallback(
    (turn: TurnItem) => {
      onTurnClick?.(turn);
    },
    [onTurnClick],
  );

  if (turns.length === 0) return null;

  const activeTurn = hoveredIndex !== null ? turns[hoveredIndex] : null;

  return (
    <div
      ref={containerRef}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      className={`codex-sidebar relative flex flex-col justify-between py-2 px-1 select-none cursor-pointer h-full min-h-[380px] max-h-[580px] w-10 shrink-0 ${className}`}
    >
      {/* Dense Vertical Rail of Subtle Tick Marks */}
      <div className="flex flex-col justify-between h-full w-full items-start pl-1 gap-[3px]">
        {Array.from({ length: totalTicks }).map((_, tickIdx) => {
          const isTurnMarker = turnMap.has(tickIdx);
          const mappedTurn = isTurnMarker ? turnMap.get(tickIdx) : null;
          const isTurnHovered = activeTurn && mappedTurn?.id === activeTurn.id;

          return (
            <div
              key={tickIdx}
              ref={(el) => {
                tickRefs.current[tickIdx] = el;
              }}
              onClick={() => {
                if (mappedTurn) handleClick(mappedTurn);
                else if (activeTurn) handleClick(activeTurn);
              }}
              className={`codex-tick transition-all duration-150 rounded-full ${
                isTurnHovered
                  ? "codex-tick--active"
                  : isTurnMarker
                    ? "codex-tick--turn"
                    : "codex-tick--subtle"
              }`}
            />
          );
        })}
      </div>

      {/* Floating Codex Popover Card (Matching the screenshot) */}
      <AnimatePresence>
        {activeTurn && (
          <motion.div
            key={activeTurn.id}
            initial={{ opacity: 0, x: -6, scale: 0.96 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -4, scale: 0.96 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            style={{ top: `${popoverTop}px`, transform: "translateY(-50%)" }}
            onClick={() => handleClick(activeTurn)}
            className="absolute left-8 z-50 w-72 sm:w-80 rounded-xl border border-white/10 bg-[#16171d]/95 backdrop-blur-xl p-3.5 shadow-2xl text-left pointer-events-auto cursor-pointer hover:border-white/20 transition-colors"
          >
            {/* Pointer arrow on left */}
            <div className="absolute -left-[5px] top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-[#16171d] border-l border-b border-white/10 rotate-45" />

            {/* Turn Title / Query */}
            <div className="relative flex items-center justify-between gap-2">
              <h4 className="text-xs font-semibold text-white tracking-tight leading-snug line-clamp-1">
                {activeTurn.query}
              </h4>
            </div>

            {/* Response Snippet Preview */}
            {activeTurn.responseSnippet && (
              <p className="text-[11px] text-slate-300/90 leading-relaxed line-clamp-2 mt-1.5 font-normal">
                {activeTurn.responseSnippet}
              </p>
            )}

            {/* Footer Metadata & Click Prompt */}
            <div className="mt-2.5 pt-1.5 border-t border-white/5 flex items-center justify-between text-[10px] text-slate-400 font-mono">
              <span className="text-slate-400 font-sans font-medium">Click to jump</span>
              {activeTurn.timestamp && <span>{activeTurn.timestamp}</span>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default LineSidebar;
