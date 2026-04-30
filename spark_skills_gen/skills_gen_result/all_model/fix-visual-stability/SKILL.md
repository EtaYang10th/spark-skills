---
id: fix-visual-stability
title: Fix Visual Stability Issues in Next.js Apps
version: 1.0.0
tags: [nextjs, react, performance, cls, foit, theme-flicker, layout-shift]
description: >
  Diagnose and fix visual stability issues in Next.js/React apps including
  Cumulative Layout Shift (CLS), Flash of Invisible Text (FOIT), and theme
  flicker on load. Covers font loading, image dimensions, async component
  placeholders, and blocking theme initialization scripts.
---

# Fix Visual Stability Issues in Next.js Apps

## Overview

Visual stability issues in Next.js apps typically fall into four categories:

1. **Theme flicker** — wrong theme renders briefly on load before JS hydrates
2. **FOIT** — fonts are invisible until loaded, causing text to flash in
3. **Image CLS** — images without explicit dimensions cause layout shifts
4. **Async component CLS** — components that fetch data render empty then shift content down

Each has a distinct fix. Diagnose all four before writing any code.

---

## High-Level Workflow

### Step 1: Audit the Codebase

Before touching anything, read all key files to understand the current state.

```bash
# Read the main layout and global styles
cat /app/src/app/layout.tsx
cat /app/src/app/globals.css

# Read theme-related components
cat /app/src/components/ThemeProvider.tsx

# Read components that render images or async data
cat /app/src/components/ProductCard.tsx
cat /app/src/components/LateBanner.tsx
cat /app/src/components/SidePane.tsx
cat /app/src/components/ResultsBar.tsx
```

Look for:
- `@font-face` declarations missing `font-display: swap`
- `ThemeProvider` that reads `localStorage` only after mount (causes flicker)
- `<img>` or `<Image>` tags without explicit `width` and `height`
- Components that conditionally render content after an async fetch with no placeholder

### Step 2: Find and Read the Tests

Locate the test files to understand exactly what the verifier checks.

```bash
# Search broadly — tests may not be in /app
find / -maxdepth 8 -name "test_*.py" -o -name "*.spec.ts" 2>/dev/null \
  | grep -v node_modules | head -20

find / -maxdepth 8 -name "playwright.config*" 2>/dev/null | grep -v node_modules

# Check for log files that reveal test commands
find /logs -type f 2>/dev/null | head -30
cat /logs/agent/command-0/command.txt 2>/dev/null
```

Common test checks in this domain:
- `test_no_theme_flicker` — verifies no flash of wrong theme on load
- `test_cls_acceptable` — verifies CLS score is below threshold
- `test_foit_prevented` — verifies `font-display: swap` is present
- `test_images_no_cls` — verifies images have explicit dimensions
- `test_app_responds_200` — basic smoke test
- `test_products_render` — verifies product components mount correctly

### Step 3: Fix FOIT — Add `font-display: swap`

In `globals.css`, every `@font-face` block must include `font-display: swap`.

```css
/* globals.css */
@font-face {
  font-family: 'YourFont';
  src: url('/fonts/yourfont.woff2') format('woff2');
  font-weight: 400;
  font-style: normal;
  font-display: swap; /* REQUIRED — prevents invisible text during font load */
}
```

Without `font-display: swap`, the browser hides text until the font loads (FOIT).
With `swap`, it shows a fallback font immediately and swaps when ready.

### Step 4: Fix Theme Flicker — Blocking Inline Script in `<head>`

The root cause: `ThemeProvider` reads `localStorage` inside a `useEffect` or
`useState` initializer that runs *after* the first paint. The page renders with
the default (light) theme, then flickers to dark.

**Two-part fix:**

**Part A — Blocking script in `layout.tsx`** (runs before any paint):

```tsx
// src/app/layout.tsx
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* BLOCKING script — must be first in <head>, before any CSS or JS */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('theme');
                  if (theme === 'dark' || theme === 'light') {
                    document.documentElement.setAttribute('data-theme', theme);
                  } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
                    document.documentElement.setAttribute('data-theme', 'dark');
                  } else {
                    document.documentElement.setAttribute('data-theme', 'light');
                  }
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
```

Key details:
- `suppressHydrationWarning` on `<html>` is required — the `data-theme` attribute
  set by the script will differ from what the server renders, and this suppresses
  the React hydration warning.
- The script must be inside `<head>`, not `<body>`, to run before paint.
- Wrap in `try/catch` to handle environments where `localStorage` is unavailable
  (e.g., private browsing with strict settings, SSR).

**Part B — `ThemeProvider` reads `localStorage` synchronously as `useState` initializer:**

```tsx
// src/components/ThemeProvider.tsx
'use client';

import { createContext, useContext, useState, useEffect } from 'react';

type Theme = 'light' | 'dark';

const ThemeContext = createContext<{
  theme: Theme;
  toggleTheme: () => void;
}>({ theme: 'light', toggleTheme: () => {} });

function getInitialTheme(): Theme {
  // Runs synchronously on client — reads localStorage before first render
  if (typeof window !== 'undefined') {
    try {
      const stored = localStorage.getItem('theme');
      if (stored === 'dark' || stored === 'light') return stored;
      if (window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
    } catch (e) {}
  }
  return 'light';
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Pass initializer function — runs once, synchronously, before first render
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('theme', theme);
    } catch (e) {}
  }, [theme]);

  const toggleTheme = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'));

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
```

Note: `ThemeProvider` should render children directly (no wrapper `<div>`).
A wrapper div can break layout tests.

### Step 5: Fix Image CLS — Explicit Dimensions

Every `<img>` or Next.js `<Image>` must have explicit `width` and `height` so
the browser reserves space before the image loads.

```tsx
// src/components/ProductCard.tsx
import Image from 'next/image';

export function ProductCard({ product }: { product: Product }) {
  return (
    <div className="product-card" data-testid="product-card">
      {/* Wrap in aspect-ratio container to prevent layout shift */}
      <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1' }}>
        <Image
          src={product.image}
          alt={product.name}
          width={300}
          height={300}
          style={{ objectFit: 'cover', width: '100%', height: '100%' }}
        />
      </div>
      <h2 className="product-name">{product.name}</h2>
      <p className="product-price">${product.price}</p>
    </div>
  );
}
```

If using a plain `<img>` tag instead of Next.js `<Image>`:

```tsx
<img
  src={product.image}
  alt={product.name}
  width={300}
  height={300}
  style={{ width: '100%', height: 'auto', display: 'block' }}
/>
```

The `width` and `height` HTML attributes tell the browser the intrinsic aspect
ratio so it can reserve space. The CSS `width: 100%` then scales it responsively.

### Step 6: Fix Async Component CLS — Reserve Space with Placeholders

Components that fetch data and conditionally render content cause CLS because
they start empty and then push other content down when data arrives.

**Pattern: render a `visibility: hidden` placeholder with the same dimensions
while loading, so the space is always reserved.**

```tsx
// src/components/LateBanner.tsx
'use client';

import { useState, useEffect } from 'react';

interface BannerData {
  message: string;
  imageUrl?: string;
}

// Define the expected rendered height so the placeholder matches
const BANNER_HEIGHT = 80; // px — match your actual rendered height

export function LateBanner() {
  const [data, setData] = useState<BannerData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/banner')
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  // Placeholder: same height, invisible — reserves space, no layout shift
  if (loading) {
    return (
      <div
        className="late-banner"
        style={{ height: BANNER_HEIGHT, visibility: 'hidden' }}
        aria-hidden="true"
      />
    );
  }

  if (!data) return null; // Data failed to load — no content, no shift (already loaded)

  return (
    <div className="late-banner" style={{ height: BANNER_HEIGHT }}>
      <p>{data.message}</p>
    </div>
  );
}
```

Apply the same pattern to `SidePane` and `ResultsBar`:

```tsx
// src/components/SidePane.tsx
'use client';

import { useState, useEffect } from 'react';

const SIDE_PANE_MIN_HEIGHT = 200; // px

export function SidePane() {
  const [filters, setFilters] = useState<string[] | null>(null);

  useEffect(() => {
    fetch('/api/filters')
      .then(r => r.json())
      .then(setFilters)
      .catch(() => setFilters([]));
  }, []);

  if (filters === null) {
    return (
      <aside
        className="side-pane"
        style={{ minHeight: SIDE_PANE_MIN_HEIGHT, visibility: 'hidden' }}
        aria-hidden="true"
      />
    );
  }

  return (
    <aside className="side-pane" style={{ minHeight: SIDE_PANE_MIN_HEIGHT }}>
      {filters.map(f => (
        <label key={f} className="filter-option">
          <input type="checkbox" value={f} /> {f}
        </label>
      ))}
    </aside>
  );
}
```

```tsx
// src/components/ResultsBar.tsx
'use client';

import { useState, useEffect } from 'react';

const RESULTS_BAR_HEIGHT = 40; // px

export function ResultsBar() {
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    fetch('/api/results-count')
      .then(r => r.json())
      .then(d => setCount(d.count))
      .catch(() => setCount(0));
  }, []);

  if (count === null) {
    return (
      <div
        className="results-bar"
        style={{ height: RESULTS_BAR_HEIGHT, visibility: 'hidden' }}
        aria-hidden="true"
      />
    );
  }

  return (
    <div className="results-bar" style={{ height: RESULTS_BAR_HEIGHT }}>
      {count} results found
    </div>
  );
}
```

### Step 7: Build and Verify

```bash
cd /app && npm run build
```

A clean build output looks like:
```
✓ Compiled successfully
✓ Linting and checking validity of types
✓ Generating static pages (4/4)
✓ Finalizing page optimization
```

If the build fails, check for:
- TypeScript errors from changed component signatures
- Missing imports after edits
- `dangerouslySetInnerHTML` syntax errors in the inline script

---

## Common Pitfalls

### 1. Theme flicker fix is incomplete without BOTH parts

The blocking `<script>` in `layout.tsx` alone is not enough if `ThemeProvider`
still initializes with `useState('light')` and then reads `localStorage` in a
`useEffect`. The component will re-render with the correct theme, but there will
still be a flash. Both the blocking script AND the synchronous `useState`
initializer are required.

### 2. Forgetting `suppressHydrationWarning` on `<html>`

The blocking script sets `data-theme` on `<html>` before React hydrates. React
will see a mismatch between the server-rendered HTML (no `data-theme`) and the
client DOM (has `data-theme`). Without `suppressHydrationWarning`, this throws
a hydration error that can break the app.

```tsx
// CORRECT
<html lang="en" suppressHydrationWarning>

// WRONG — will throw hydration warning
<html lang="en">
```

### 3. Wrapping `ThemeProvider` children in a `<div>`

Adding a wrapper `<div>` inside `ThemeProvider` can break layout tests that
check the DOM structure. Use a fragment or render children directly.

```tsx
// CORRECT
return (
  <ThemeContext.Provider value={{ theme, toggleTheme }}>
    {children}
  </ThemeContext.Provider>
);

// WRONG — extra div breaks layout tests
return (
  <ThemeContext.Provider value={{ theme, toggleTheme }}>
    <div>{children}</div>
  </ThemeContext.Provider>
);
```

### 4. Using `display: none` instead of `visibility: hidden` for placeholders

`display: none` removes the element from layout flow entirely — it reserves no
space. `visibility: hidden` keeps the element in the flow but makes it invisible.
For CLS prevention, you need `visibility: hidden`.

```tsx
// CORRECT — reserves space
style={{ height: 80, visibility: 'hidden' }}

// WRONG — no space reserved, content shifts when data loads
style={{ display: 'none' }}
```

### 5. Not matching placeholder height to actual rendered height

If the placeholder height doesn't match the actual content height, there will
still be a layout shift when the content loads. Measure the actual rendered
height of the component and use that value for the placeholder.

### 6. Changing class names, IDs, or `data-testid` attributes

Tests rely on these selectors. Never rename them. Only change styles, logic,
and structure — not identifiers.

### 7. CLS from images is a separate test from CLS from async components

`test_images_no_cls` and `test_cls_acceptable` are different checks. Fixing
image dimensions alone may not fix the overall CLS score if async components
are also shifting layout. Fix all sources of CLS.

### 8. `font-display: swap` must be on every `@font-face` block

If there are multiple `@font-face` declarations (e.g., different weights or
styles), each one needs `font-display: swap`. A single missing declaration can
still cause FOIT for that weight.

---

## Quick Diagnostic Checklist

Before writing any code, verify each of these:

- [ ] `globals.css` — every `@font-face` has `font-display: swap`
- [ ] `layout.tsx` — blocking `<script>` in `<head>` sets `data-theme` from `localStorage`
- [ ] `layout.tsx` — `<html>` has `suppressHydrationWarning`
- [ ] `ThemeProvider.tsx` — `useState` uses a synchronous initializer function, not `'light'`
- [ ] `ProductCard.tsx` (or equivalent) — all `<img>`/`<Image>` have explicit `width` and `height`
- [ ] Async components (`LateBanner`, `SidePane`, `ResultsBar`, etc.) — render `visibility: hidden` placeholder with correct dimensions while loading
- [ ] No class names, IDs, or `data-testid` attributes were changed
- [ ] `npm run build` exits with code 0
