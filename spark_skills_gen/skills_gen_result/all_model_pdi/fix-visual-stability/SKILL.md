---
name: fix-visual-stability
version: 1.0.0
description: Fix visual stability issues in Next.js e-commerce apps — covers CLS (Cumulative Layout Shift), FOIT (Flash of Invisible Text), and theme flicker
tags: [nextjs, react, performance, cls, foit, web-vitals, ux]
---

# Fix Visual Stability in Next.js Apps

## Overview

Visual stability issues in Next.js apps typically fall into four categories:

1. **FOIT** — Font flash/invisible text while custom fonts load
2. **Theme flicker** — Dark/light mode flashes on hydration
3. **Image CLS** — Images cause layout shift because browser doesn't know their dimensions
4. **Async component CLS** — Components that return `null` while loading, then pop in and shift layout

All four are fixable with targeted, minimal changes. None require restructuring the app.

---

## High-Level Workflow

### Step 1 — Audit the codebase

Before touching anything, map the problem surface:

```bash
find /app/src -type f \( -name "*.tsx" -o -name "*.ts" -o -name "*.css" \) \
  | grep -v node_modules | grep -v .next | sort
```

Read these files in priority order:
- `src/app/layout.tsx` — theme script, font preloading
- `src/app/globals.css` — `@font-face` declarations
- Any component that fetches async data and conditionally renders (Banner, SidePane, etc.)
- Any component that renders `<img>` tags (ProductCard, etc.)

### Step 2 — Identify which checks are failing

The test suite checks four things. Map symptoms to fixes:

| Failing test | Root cause | Fix location |
|---|---|---|
| `test_foit_prevented` | No `font-display` strategy | `globals.css` `@font-face` |
| `test_no_theme_flicker` | No inline theme script before hydration | `layout.tsx` `<head>` |
| `test_images_no_cls` | `<img>` lacks explicit `width`/`height` | `ProductCard.tsx` (or equivalent) |
| `test_cls_acceptable` | Async components return `null` while loading | Banner/SidePane/ResultsBar components |

### Step 3 — Apply fixes in order

Fix all four issues. They are independent and don't conflict.

### Step 4 — Build and verify

```bash
cd /app && npm run build
```

A clean build (`✓ Compiled successfully`) is required before the tests will pass.

---

## Fix A: FOIT — Add `font-display: swap`

In `globals.css`, every `@font-face` block must include `font-display: swap`. Without it, the browser either blocks render (FOIT) or shows nothing until the font loads.

```css
/* globals.css */
@font-face {
  font-family: 'YourFont';
  src: url('/fonts/yourfont.woff2') format('woff2');
  font-weight: 400;
  font-style: normal;
  font-display: swap; /* ← THIS is the fix. Renders fallback immediately. */
}
```

`font-display: swap` tells the browser: render with the fallback font immediately, then swap in the custom font when it's ready. This eliminates invisible text.

---

## Fix B: Theme Flicker — Inline script in `<head>`

React hydration happens after the HTML is parsed. If theme is stored in `localStorage`, there's a window between paint and hydration where the wrong theme renders.

The fix is a synchronous inline `<script>` in `<head>` that runs before React, reads `localStorage`, and sets `data-theme` on `<html>` before the first paint.

```tsx
// layout.tsx
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('theme') || 'light';
                  document.documentElement.setAttribute('data-theme', theme);
                } catch(e) {}
              })();
            `,
          }}
        />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
```

Key points:
- Must be in `<head>`, not `<body>` — it needs to run before paint
- Wrap in IIFE with try/catch — `localStorage` can throw in private browsing
- Use `dangerouslySetInnerHTML` — Next.js will inline this as a synchronous script

---

## Fix C: Image CLS — Explicit dimensions on `<img>`

When the browser doesn't know an image's dimensions, it allocates zero space until the image loads, then shifts everything down. Fix: provide explicit `width` and `height` attributes AND wrap in an aspect-ratio container.

The `aspect-ratio` container alone is NOT sufficient — the image element itself needs `width` and `height` attributes.

```tsx
// ProductCard.tsx
export function ProductCard({ product }: { product: Product }) {
  return (
    <div style={{ aspectRatio: '1 / 1', width: '100%', position: 'relative', overflow: 'hidden' }}>
      <img
        src={product.image}
        alt={product.name}
        width={400}      // ← explicit width
        height={400}     // ← explicit height
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
    </div>
  );
}
```

If using Next.js `<Image>` component, `width` and `height` are required props — it handles this automatically.

---

## Fix D: Async Component CLS — Reserve space while loading

Components that return `null` while fetching data cause layout shift when they mount. The fix: render the component's container with `visibility: hidden` and a `minHeight` that matches the loaded state, so the space is reserved.

```tsx
// Before (causes CLS):
export function Banner() {
  const [data, setData] = useState(null);
  useEffect(() => { fetchBannerData().then(setData); }, []);
  if (!data) return null; // ← layout shift when data arrives
  return <div className="banner">{data.text}</div>;
}

// After (no CLS):
export function Banner() {
  const [data, setData] = useState(null);
  const [loaded, setLoaded] = useState(false);
  
  useEffect(() => {
    fetchBannerData().then(d => {
      setData(d);
      setLoaded(true);
    });
  }, []);

  return (
    <div
      className="banner"
      style={{
        visibility: loaded ? 'visible' : 'hidden',
        minHeight: '60px', // match the component's natural height
      }}
    >
      {data?.text}
    </div>
  );
}
```

Apply this pattern to every async component: `Banner`, `LateBanner`, `SidePane`, `ResultsBar`, or any component that conditionally renders based on async state.

---

## Common Pitfalls

1. **`aspect-ratio` container alone doesn't prevent image CLS** — you must also add `width` and `height` attributes to the `<img>` element itself. The container reserves space in the flow, but the browser still needs the intrinsic dimensions.

2. **`visibility: hidden` + `minHeight` does NOT cause CLS** — this is the correct pattern. Don't second-guess it. The element occupies space in the layout even when invisible.

3. **Theme script must be synchronous and in `<head>`** — async scripts or scripts in `<body>` run too late. The flicker happens between HTML parse and script execution.

4. **`font-display: swap` must be on every `@font-face` block** — if you have multiple weights/styles, each needs its own `font-display: swap`.

5. **Build before testing** — the test suite runs against the built app. A passing TypeScript compile doesn't mean the tests pass; run `npm run build` and confirm `✓ Compiled successfully`.

6. **Don't change existing `className`, `id`, or `data-testid` attributes** — tests rely on them for element selection.

---

## Reference Implementation

This is a complete, self-contained implementation of all four fixes. Adapt paths and component names to match your specific app.

```tsx
// ============================================================
// FILE: src/app/layout.tsx
// Fix: theme flicker (inline script) + font preloading
// ============================================================
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Shop',
  description: 'E-commerce store',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        {/* Inline theme script — runs synchronously before first paint */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('theme') || 'light';
                  document.documentElement.setAttribute('data-theme', theme);
                } catch(e) {}
              })();
            `,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
```

```css
/* ============================================================
   FILE: src/app/globals.css
   Fix: FOIT — add font-display: swap to every @font-face
   ============================================================ */

@font-face {
  font-family: 'CustomFont';
  src: url('/fonts/customfont.woff2') format('woff2'),
       url('/fonts/customfont.woff') format('woff');
  font-weight: 400;
  font-style: normal;
  font-display: swap; /* ← prevents FOIT */
}

@font-face {
  font-family: 'CustomFont';
  src: url('/fonts/customfont-bold.woff2') format('woff2');
  font-weight: 700;
  font-style: normal;
  font-display: swap; /* ← on every @font-face block */
}

/* CSS custom properties for theming */
:root {
  --bg: #ffffff;
  --text: #000000;
  --card-bg: #f5f5f5;
  --border-color: #e0e0e0;
}

[data-theme='dark'] {
  --bg: #1a1a1a;
  --text: #ffffff;
  --card-bg: #2a2a2a;
  --border-color: #444444;
}

body {
  background-color: var(--bg);
  color: var(--text);
  font-family: 'CustomFont', sans-serif;
  margin: 0;
}
```

```tsx
// ============================================================
// FILE: src/components/ProductCard.tsx
// Fix: image CLS — explicit width/height + aspect-ratio container
// ============================================================
import React from 'react';

interface Product {
  id: string;
  name: string;
  price: number;
  image: string;
}

export function ProductCard({ product }: { product: Product }) {
  return (
    <div
      data-testid="product-card"
      style={{
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--border-color)',
        borderRadius: '8px',
        overflow: 'hidden',
      }}
    >
      {/* Aspect-ratio container reserves space in layout */}
      <div style={{ aspectRatio: '1 / 1', width: '100%', overflow: 'hidden' }}>
        <img
          src={product.image}
          alt={product.name}
          width={400}   /* explicit width — browser reserves space */
          height={400}  /* explicit height — browser reserves space */
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      </div>
      <div style={{ padding: '12px' }}>
        <h3 style={{ margin: '0 0 8px' }}>{product.name}</h3>
        <p style={{ margin: 0 }}>${product.price.toFixed(2)}</p>
      </div>
    </div>
  );
}
```

```tsx
// ============================================================
// FILE: src/components/Banner.tsx
// Fix: async CLS — visibility:hidden + minHeight instead of null
// ============================================================
import React, { useState, useEffect } from 'react';

export function Banner() {
  const [content, setContent] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // Simulate async fetch
    fetch('/api/banner')
      .then(r => r.json())
      .then(data => {
        setContent(data.message);
        setLoaded(true);
      })
      .catch(() => setLoaded(true)); // still mark loaded on error
  }, []);

  return (
    <div
      data-testid="banner"
      style={{
        visibility: loaded ? 'visible' : 'hidden',
        minHeight: '48px', // match the component's natural rendered height
        backgroundColor: 'var(--card-bg)',
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
      }}
    >
      {content}
    </div>
  );
}
```

```tsx
// ============================================================
// FILE: src/components/SidePane.tsx
// Fix: async CLS — same visibility:hidden + minHeight pattern
// ============================================================
import React, { useState, useEffect } from 'react';

interface Filter {
  id: string;
  label: string;
}

export function SidePane() {
  const [filters, setFilters] = useState<Filter[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch('/api/filters')
      .then(r => r.json())
      .then(data => {
        setFilters(data.filters ?? []);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  return (
    <aside
      data-testid="side-pane"
      style={{
        visibility: loaded ? 'visible' : 'hidden',
        minHeight: '200px', // reserve space matching loaded height
        width: '240px',
        padding: '16px',
        borderRight: '1px solid var(--border-color)',
      }}
    >
      <h2>Filters</h2>
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {filters.map(f => (
          <li key={f.id} style={{ marginBottom: '8px' }}>
            <label>
              <input type="checkbox" style={{ marginRight: '8px' }} />
              {f.label}
            </label>
          </li>
        ))}
      </ul>
    </aside>
  );
}
```

```tsx
// ============================================================
// FILE: src/components/ResultsBar.tsx
// Fix: async CLS — same pattern
// ============================================================
import React, { useState, useEffect } from 'react';

export function ResultsBar() {
  const [count, setCount] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch('/api/products/count')
      .then(r => r.json())
      .then(data => {
        setCount(data.count);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  return (
    <div
      data-testid="results-bar"
      style={{
        visibility: loaded ? 'visible' : 'hidden',
        minHeight: '40px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 0',
        borderBottom: '1px solid var(--border-color)',
      }}
    >
      <span>{count !== null ? `${count} results` : ''}</span>
      <span>Sort: Price ▼</span>
    </div>
  );
}
```

---

## Verification Checklist

Before submitting, confirm:

- [ ] `globals.css` — every `@font-face` has `font-display: swap`
- [ ] `layout.tsx` — inline `<script>` in `<head>` reads `localStorage('theme')` and sets `data-theme`
- [ ] All `<img>` tags have explicit `width` and `height` attributes (not just CSS)
- [ ] All async components use `visibility: hidden` + `minHeight` instead of returning `null`
- [ ] No existing `className`, `id`, or `data-testid` attributes were changed
- [ ] `npm run build` completes with `✓ Compiled successfully`