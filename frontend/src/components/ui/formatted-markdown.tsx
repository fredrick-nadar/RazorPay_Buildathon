"use client";

import React from "react";

interface FormattedMarkdownProps {
  content: string;
  className?: string;
}

/**
 * High-performance, zero-dependency Markdown renderer tailored for financial reconciliation.
 * Accurately styles bold text, bullet points, headers, inline codes, currency badges, and tables.
 */
export function FormattedMarkdown({ content, className = "" }: FormattedMarkdownProps) {
  if (!content) return null;

  // Split into paragraphs / lines
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let inList = false;
  let listItems: React.ReactNode[] = [];

  function flushList() {
    if (inList && listItems.length > 0) {
      elements.push(
        <ul key={`list-${elements.length}`} className="my-2.5 space-y-1.5 pl-5 list-disc text-slate-700">
          {listItems}
        </ul>
      );
      listItems = [];
      inList = false;
    }
  }

  lines.forEach((line, index) => {
    const trimmed = line.trim();

    // Empty line
    if (!trimmed) {
      flushList();
      elements.push(<div key={`space-${index}`} className="h-2" />);
      return;
    }

    // Header 3: ### Header
    if (trimmed.startsWith("### ")) {
      flushList();
      elements.push(
        <h3
          key={`h3-${index}`}
          className="mt-4 mb-2 text-sm font-bold tracking-tight text-slate-900 flex items-center gap-2"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-slate-900 inline-block" />
          {renderInline(trimmed.substring(4))}
        </h3>
      );
      return;
    }

    // Header 2: ## Header
    if (trimmed.startsWith("## ")) {
      flushList();
      elements.push(
        <h2
          key={`h2-${index}`}
          className="mt-5 mb-2.5 text-base font-bold tracking-tight text-slate-950 flex items-center gap-2"
        >
          <span className="h-2 w-2 rounded-full bg-indigo-600 inline-block" />
          {renderInline(trimmed.substring(3))}
        </h2>
      );
      return;
    }

    // Bullet List Item: - or *
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      inList = true;
      listItems.push(
        <li key={`li-${index}`} className="text-xs sm:text-sm leading-relaxed text-slate-800">
          {renderInline(trimmed.substring(2))}
        </li>
      );
      return;
    }

    // Numbered List Item: 1. 2.
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    if (numMatch) {
      flushList();
      elements.push(
        <div key={`num-${index}`} className="my-1 flex items-start gap-2.5 text-xs sm:text-sm text-slate-800">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-bold text-slate-700">
            {numMatch[1]}
          </span>
          <span className="leading-relaxed pt-0.5">{renderInline(numMatch[2] ?? "")}</span>
        </div>
      );
      return;
    }

    // Normal paragraph line
    flushList();
    elements.push(
      <p key={`p-${index}`} className="my-1.5 text-xs sm:text-sm leading-relaxed text-slate-800">
        {renderInline(trimmed)}
      </p>
    );
  });

  flushList();

  return <div className={`prose-sm max-w-none text-slate-900 ${className}`}>{elements}</div>;
}

/**
 * Render inline tokens: **bold**, `code`, and currency/percentage badges.
 */
function renderInline(text: string): React.ReactNode {
  // Regex to split by bold (**text**) or inline code (`text`)
  const parts = text.split(/(\*\*.*?\*\*|`.*?`)/g);

  return parts.map((part, i) => {
    if (!part) return null;

    // Bold text: **bold**
    if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
      const inner = part.slice(2, -2);
      // Check if it's a financial figure or percentage
      if (inner.startsWith("₹") || inner.includes("paise") || inner.includes("%")) {
        return (
          <span
            key={i}
            className="font-bold text-slate-950 inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded bg-slate-100/90 border border-slate-200/80 text-[12px] sm:text-[13px]"
          >
            {inner}
          </span>
        );
      }
      return (
        <strong key={i} className="font-bold text-slate-950">
          {inner}
        </strong>
      );
    }

    // Inline code: `code`
    if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
      const code = part.slice(1, -1);
      return (
        <code
          key={i}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] sm:text-xs font-semibold text-slate-800 border border-slate-200"
        >
          {code}
        </code>
      );
    }

    return part;
  });
}
