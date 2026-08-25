"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LandingArrow } from "./landing-arrow";

const NAV_LINKS = [
  { label: "Platform", href: "#platform" },
  { label: "Safety", href: "#safety" },
  { label: "Benchmark", href: "#benchmark" },
] as const;

export function LandingNav() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <header className="nav">
        <nav className="nav__links" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a key={link.href} href={link.href}>
              {link.label}
            </a>
          ))}
        </nav>

        <Link className="logo" href="/" aria-label="ARGUS CONTROL">
          <svg viewBox="0 0 42 34" fill="currentColor" aria-hidden="true">
            <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
            <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
            <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
            <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
            <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
            <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
          </svg>
        </Link>

        <div className="nav__right">
          <Link className="btn btn--nav nav__cta" href="/dashboard">
            <span className="btn__label">Open Control Room</span>
            <span className="btn__icon" aria-hidden="true">
              <LandingArrow />
            </span>
          </Link>
          <button
            className="nav__burger"
            type="button"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            aria-controls="mobile-menu"
            onClick={() => setOpen((value) => !value)}
          >
            <span></span>
            <span></span>
            <span></span>
          </button>
        </div>
      </header>

      <nav id="mobile-menu" className="mobile-menu" hidden={!open}>
        {NAV_LINKS.map((link) => (
          <a key={link.href} href={link.href} onClick={() => setOpen(false)}>
            {link.label}
          </a>
        ))}
        <Link className="btn btn--light" href="/dashboard" onClick={() => setOpen(false)}>
          <span className="btn__label">Open Control Room</span>
          <span className="btn__icon" aria-hidden="true">
            <LandingArrow />
          </span>
        </Link>
      </nav>
    </>
  );
}
