---
title: Next.js React Performance Debugging for E-commerce Apps
summary: Diagnose and fix slow catalog, cart, compare-page, and API-route performance issues in Next.js by targeting upstream fetch patterns, non-blocking side effects, bundle size, and unnecessary rerenders without breaking required test hooks or instrumentation.
category: react-performance-debugging
tags:
  - nextjs
  - react
  - performance
  - api-routes
  - memoization
  - bundle-size
  - ecommerce
  - profiling
applies_to:
  - Slow product listing pages
  - Slow add-to-cart interactions
  - Compare/detail pages with heavy client bundles
  - Next.js route handlers with slow responses
prerequisites:
  - Node.js and npm
  - Ability to run Next.js build/start locally
  - Access to source code for app routes, API routes, and client components
---

# Next.js React Performance Debugging for E-commerce Apps

Use this skill when a Next.js storefront feels slow in three common places:

1. **Catalog/homepage load**
2. **Cart interactions**
3. **Compare/details pages**
4. **API routes that proxy upstream services**

The winning pattern is usually **not** a single micro-optimization. It is a coordinated fix across:

- upstream request strategy
- route handler behavior
- client rerender control
- bundle trimming
- final smoke verification

This skill is especially useful when the app must remain functionally identical and you must **not** remove test hooks like `data-testid` or instrumentation like `performance.mark()`.

---

## High-Level Workflow

### 1) Map the slow path before editing code
Inspect the repository to find:

- homepage data flow
- product API route
- external API client/service layer
- cart state propagation
- compare page imports and computations
- any required test IDs or performance instrumentation

Why: many slow apps have multiple independent bottlenecks. If you optimize only rendering but the route still blocks on analytics or sequential upstream calls, the app remains slow.

Use fast code search first:

```bash
#!/usr/bin/env bash
set -euo pipefail

printf '\n== key files ==\n'
rg --files -g 'package.json' -g 'next.config.*' -g 'src/**/*' -g 'app/**/*' .

printf '\n== relevant symbols ==\n'
rg -n \
  "api/products|api/checkout|EXTERNAL_API_URL|performance\.mark|data-testid|add-to-cart|compare|Advanced|analytics|fetch\(" \
  src app . || true
```

Decision criteria:

- If the homepage or route does multiple `await` calls in sequence, parallelize.
- If analytics/logging blocks the response, make it fire-and-forget.
- If the compare page imports large client-side libraries (`lodash`, `mathjs`, charting, utility megabundles), replace with local helpers or lazy loading.
- If cart updates rerender the entire product grid, memoize and stabilize props.

---

### 2) Confirm the upstream API is actually used
A frequent mistake is âoptimizingâ by serving local/static data, but validators may require a **real external API call**.

Why: a fake fast path may pass visual checks but fail hidden performance/integration checks.

Use an API client wrapper with explicit base URL and safe fetch behavior:

```ts
// src/services/api-client.ts
export type Product = {
  id: string;
  name: string;
  price: number;
  category?: string;
  image?: string;
  reviews?: Array<{ rating: number }>;
};

type FetchJsonOptions = {
  revalidate?: number;
  cache?: RequestCache;
  tags?: string[];
};

const API_BASE_URL = process.env.EXTERNAL_API_URL;

if (!API_BASE_URL) {
  console.warn("EXTERNAL_API_URL is not set; external fetches will fail.");
}

async function fetchJson<T>(
  path: string,
  options: FetchJsonOptions = {}
): Promise<T> {
  if (!API_BASE_URL) {
    throw new Error("Missing EXTERNAL_API_URL");
  }

  const url = new URL(path, API_BASE_URL).toString();

  const res = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: options.cache,
    next: options.revalidate
      ? { revalidate: options.revalidate, tags: options.tags }
      : options.tags
      ? { tags: options.tags }
      : undefined,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Upstream request failed: ${res.status} ${url} ${body}`);
  }

  return (await res.json()) as T;
}

export async function getProducts(): Promise<Product[]> {
  return fetchJson<Product[]>("/products", {
    revalidate: 60,
    tags: ["products"],
  });
}

export async function getUserProfile<T>(): Promise<T> {
  return fetchJson<T>("/user/profile", {
    cache: "no-store",
  });
}

export async function sendAnalytics(payload: Record<string, unknown>) {
  if (!API_BASE_URL) return;

  try {
    await fetch(new URL("/analytics", API_BASE_URL).toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify(payload),
    });
  } catch (error) {
    console.error("Analytics request failed", error);
  }
}
```

Critical rule: preserve the real external path and response contract expected by the app.

---

### 3) Parallelize independent server-side fetches
Check app pages and route handlers for waterfall requests.

Bad pattern:

```ts
const user = await getUserProfile();
const products = await getProducts();
const config = await getConfig();
```

Good pattern:

```ts
// src/app/page.tsx
import { getProducts, getUserProfile } from "@/services/api-client";

export default async function Page() {
  const [user, products] = await Promise.all([
    getUserProfile<{ name?: string }>().catch(() => null),
    getProducts(),
  ]);

  return (
    <main>
      <h1>Products</h1>
      {user?.name ? <p>Welcome, {user.name}</p> : null}
      <ul>
        {products.map((product) => (
          <li key={product.id}>{product.name}</li>
        ))}
      </ul>
    </main>
  );
}
```

Why: independent requests should not wait for one another. On homepage and route handlers this is often the easiest high-impact win.

Guardrails:

- Use `Promise.all` only for truly independent requests.
- If one call is optional, use `.catch(() => fallback)` on that call rather than failing the whole page unnecessarily.
- Do not change visible behavior or response schema unless required.

---

### 4) Make route handlers fast by removing blocking side effects
API routes often become slow because they wait for analytics, logging, or non-critical enrichment before responding.

Route handlers should:
- fetch required upstream data concurrently
- return the main response as soon as required data is ready
- send non-critical analytics in the background
- keep response schema stable

Example:

```ts
// src/app/api/products/route.ts
import { NextResponse } from "next/server";
import { getProducts, getUserProfile, sendAnalytics } from "@/services/api-client";

export async function GET() {
  try {
    const [products, user] = await Promise.all([
      getProducts(),
      getUserProfile<{ id?: string; segment?: string }>().catch(() => null),
    ]);

    void sendAnalytics({
      event: "products_api_view",
      userId: user?.id ?? null,
      count: products.length,
      ts: Date.now(),
    });

    return NextResponse.json(
      { products, user },
      {
        status: 200,
        headers: {
          "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300",
        },
      }
    );
  } catch (error) {
    console.error("GET /api/products failed", error);
    return NextResponse.json(
      { error: "Failed to load products" },
      { status: 500 }
    );
  }
}
```

Why: analytics should not determine customer-visible latency.

Important:
- Do not accidentally turn the endpoint into a static/local data source if external calls are required.
- Preserve JSON field names expected by pages and tests.

---

### 5) Reduce rerenders in product grids and cart interactions
Slow add-to-cart is often caused by rendering the entire list on every cart change.

Target these issues:
- derived data recalculated on every render
- unstable callbacks recreated every render
- expensive membership checks using `Array.includes` repeatedly
- child components receiving new object/function props each render

Use `useMemo`, `useCallback`, and `React.memo`.

```tsx
// src/components/ProductList.tsx
"use client";

import { useCallback, useMemo, useState } from "react";
import ProductCard from "@/components/ProductCard";
import type { Product } from "@/services/api-client";

type Props = {
  products: Product[];
};

export default function ProductList({ products }: Props) {
  const [cartIds, setCartIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");

  const cartIdSet = useMemo(() => new Set(cartIds), [cartIds]);

  const reviewCountById = useMemo(() => {
    const map = new Map<string, number>();
    for (const product of products) {
      map.set(product.id, product.reviews?.length ?? 0);
    }
    return map;
  }, [products]);

  const filteredProducts = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return products;
    return products.filter((p) =>
      `${p.name} ${p.category ?? ""}`.toLowerCase().includes(normalized)
    );
  }, [products, query]);

  const handleAddToCart = useCallback((productId: string) => {
    setCartIds((prev) => (prev.includes(productId) ? prev : [...prev, productId]));
  }, []);

  return (
    <section>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search products"
      />

      <div className="grid">
        {filteredProducts.map((product) => (
          <ProductCard
            key={product.id}
            product={product}
            reviewCount={reviewCountById.get(product.id) ?? 0}
            inCart={cartIdSet.has(product.id)}
            onAddToCart={handleAddToCart}
          />
        ))}
      </div>
    </section>
  );
}
```

Why this works:
- `Set.has` is cheaper and clearer for repeated cart membership checks.
- derived review counts and filters do not recompute unnecessarily
- stable `onAddToCart` lets memoized child cards skip rerendering

---

### 6) Memoize product cards but preserve instrumentation and test hooks
If `ProductCard` contains `performance.mark()`, keep it. Do not remove required instrumentation or `data-testid` attributes.

Use `React.memo` with a targeted props comparison:

```tsx
// src/components/ProductCard.tsx
"use client";

import React, { memo, useCallback } from "react";
import type { Product } from "@/services/api-client";

type Props = {
  product: Product;
  reviewCount: number;
  inCart: boolean;
  onAddToCart: (productId: string) => void;
};

function ProductCardComponent({
  product,
  reviewCount,
  inCart,
  onAddToCart,
}: Props) {
  performance.mark(`ProductCard-render-${product.id}`);

  const handleClick = useCallback(() => {
    onAddToCart(product.id);
  }, [onAddToCart, product.id]);

  return (
    <article data-testid={`product-card-${product.id}`}>
      <h2>{product.name}</h2>
      <p>${product.price.toFixed(2)}</p>
      <p>{reviewCount} reviews</p>
      <button
        data-testid={`add-to-cart-${product.id}`}
        disabled={inCart}
        onClick={handleClick}
      >
        {inCart ? "In Cart" : "Add to Cart"}
      </button>
    </article>
  );
}

const ProductCard = memo(
  ProductCardComponent,
  (prev, next) =>
    prev.product === next.product &&
    prev.reviewCount === next.reviewCount &&
    prev.inCart === next.inCart &&
    prev.onAddToCart === next.onAddToCart
);

export default ProductCard;
```

Notes:
- If `product` objects are recreated each render upstream, memoization will not help. Stabilize them first.
- Preserve `data-testid` values exactly if validators use them.
- Keep `performance.mark()` intact even if it looks noisy.

---

### 7) Trim compare-page bundle size
Compare pages often become slow because they import big client-only libraries for simple math/utility work.

Replace this kind of pattern:
- `lodash` for `groupBy`, `sortBy`, `mean`
- `mathjs` for trivial averages/percentages
- large chart/data libs rendered immediately on first load

With local helpers and memoization:

```tsx
// src/app/compare/page.tsx
"use client";

import { useMemo, useState } from "react";
import type { Product } from "@/services/api-client";

function average(values: number[]): number {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function AdvancedAnalysis({ products }: { products: Product[] }) {
  const metrics = useMemo(() => {
    const prices = products.map((p) => p.price ?? 0);
    const reviewAverages = products.map((p) =>
      average((p.reviews ?? []).map((r) => r.rating ?? 0))
    );

    return {
      averagePrice: average(prices),
      averageRating: average(reviewAverages),
      expensiveShare:
        products.length === 0
          ? 0
          : products.filter((p) => (p.price ?? 0) > average(prices)).length /
            products.length,
    };
  }, [products]);

  return (
    <section data-testid="advanced-content">
      <p>Average price: ${metrics.averagePrice.toFixed(2)}</p>
      <p>Average rating: {metrics.averageRating.toFixed(2)}</p>
      <p>Premium share: {formatPercent(metrics.expensiveShare)}</p>
    </section>
  );
}

export default function ComparePage() {
  const [activeTab, setActiveTab] = useState<"overview" | "advanced">("overview");
  const products: Product[] = [];

  return (
    <main>
      <nav>
        <button data-testid="tab-overview" onClick={() => setActiveTab("overview")}>
          Overview
        </button>
        <button data-testid="tab-advanced" onClick={() => setActiveTab("advanced")}>
          Advanced Analysis
        </button>
      </nav>

      {activeTab === "advanced" ? (
        <AdvancedAnalysis products={products} />
      ) : (
        <div>Overview content</div>
      )}
    </main>
  );
}
```

Why: shipping a large utility bundle to the browser for simple aggregation is a common hidden cause of slow compare pages.

If a heavy visualization is truly needed:
- lazy load with `next/dynamic`
- render only when the advanced tab is opened
- keep the `data-testid="advanced-content"` container intact

---

### 8) Validate production behavior, not just dev behavior
Always verify with a production build. Dev mode can mask or exaggerate performance issues.

Run:

```bash
#!/usr/bin/env bash
set -euo pipefail

npm run build
npm run start -- --port 3000
```

Then smoke test critical flows:

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:3000}"

echo "Checking homepage..."
curl -fsSL "$BASE_URL/" | head -n 40

echo
echo "Checking products API..."
curl -fsSL "$BASE_URL/api/products" | python3 -m json.tool | head -n 60

echo
echo "Checking compare page..."
curl -fsSL "$BASE_URL/compare" | grep -n "tab-advanced\|advanced-content" || true
```

If you need a browser-level smoke test:

```js
// verify-flows.js
const { chromium } = require("playwright");

(async () => {
  const baseURL = process.env.BASE_URL || "http://127.0.0.1:3000";
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(baseURL, { waitUntil: "networkidle" });

  const productCard = page.locator('[data-testid^="product-card-"]').first();
  await productCard.waitFor({ state: "visible", timeout: 15000 });

  const addToCart = page.locator('[data-testid^="add-to-cart-"]').first();
  if (await addToCart.count()) {
    await addToCart.click();
  }

  await page.goto(`${baseURL}/compare`, { waitUntil: "networkidle" });
  await page.locator('[data-testid="tab-advanced"]').click();
  await page.locator('[data-testid="advanced-content"]').waitFor({
    state: "visible",
    timeout: 15000,
  });

  console.log("Smoke checks passed");
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
```

Verification checklist:
- homepage renders product data
- add-to-cart still works
- compare advanced tab renders and keeps `data-testid="advanced-content"`
- `/api/products` returns real populated data
- no build errors
- no accidental removal of required instrumentation or test IDs

---

## Concrete Fix Patterns

### Pattern A: Move shared fetch logic into a typed API client
This prevents duplicated slow fetch behavior and makes caching intentional.

```ts
// src/services/api-client.ts
export async function fetchWithTimeout<T>(
  input: RequestInfo | URL,
  init: RequestInit & { timeoutMs?: number } = {}
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), init.timeoutMs ?? 10000);

  try {
    const res = await fetch(input, { ...init, signal: controller.signal });
    if (!res.ok) {
      throw new Error(`Request failed: ${res.status}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}
```

Use timeouts for upstream resilience, but do not silently replace required external calls with fake local data.

---

### Pattern B: Fire-and-forget analytics safely
Non-critical telemetry should never block customer-facing routes.

```ts
// src/lib/non-blocking.ts
export function runInBackground(task: Promise<unknown>, label: string) {
  void task.catch((error) => {
    console.error(`Background task failed: ${label}`, error);
  });
}
```

Usage:

```ts
import { runInBackground } from "@/lib/non-blocking";
import { sendAnalytics } from "@/services/api-client";

runInBackground(
  sendAnalytics({ event: "products_loaded", ts: Date.now() }),
  "products_loaded"
);
```

---

### Pattern C: Prefer local helpers over large client libraries
For simple aggregation, write tiny local utilities.

```ts
// src/lib/stats.ts
export function sum(values: number[]): number {
  return values.reduce((acc, value) => acc + value, 0);
}

export function mean(values: number[]): number {
  return values.length ? sum(values) / values.length : 0;
}

export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}
```

This often replaces `mathjs` or broad `lodash` imports.

---

### Pattern D: Stabilize props for memoized children
`React.memo` is only effective when parents pass stable values.

```tsx
// Stable callback and primitive props
const handleAddToCart = useCallback((id: string) => {
  setCartIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
}, []);

<ProductCard
  product={product}
  inCart={cartIdSet.has(product.id)}
  reviewCount={reviewCountById.get(product.id) ?? 0}
  onAddToCart={handleAddToCart}
/>
```

Avoid inline object props like:

```tsx
// Avoid: new object every render
<ProductCard meta={{ inCart: cartIdSet.has(product.id) }} />
```

---

## Common Pitfalls

### 1) Optimizing UI only, while the route still bypasses the real external API
Hidden validators may require proof that the app still performs actual upstream requests. Keep the external client path intact.

### 2) Waiting for analytics/logging before returning the API response
This is a classic route-latency bug. Analytics should be background work.

### 3) Using sequential `await` for independent requests
If user/profile/products/config are independent, fetch them concurrently.

### 4) Breaking the response contract while âoptimizingâ
Do not rename fields, change nesting, or switch from object to array unless the app is updated everywhere.

### 5) Removing `data-testid` attributes or instrumentation
If the task forbids changing `data-testid` values or removing `performance.mark()`, preserve them exactly.

### 6) Memoizing child components without stabilizing parent props
`React.memo` alone does little if callbacks, arrays, or objects are recreated every render.

### 7) Replacing large client libraries with underpowered shortcuts that change behavior
Use small local helpers only when equivalent behavior is sufficient. Do not degrade correctness to save bytes.

### 8) Testing only in `next dev`
Always run `npm run build` and `npm run start`. Production behavior matters most for performance and hidden validation.

### 9) Assuming browser automation is required
If Playwright/Chromium is unavailable, use `curl`, page HTML inspection, and API route verification. But still validate the compare-tab test target and add-to-cart functionality by whatever tooling is available.

---

## Reference Implementation

The following example shows an end-to-end performance-oriented structure for a Next.js storefront. It includes:

- a typed external API client
- a fast `/api/products` route
- a homepage that parallelizes fetches
- a memoized product list/card flow
- a compare page that avoids heavy client libraries
- a minimal verification script

Copy, adapt paths/contracts, and preserve any required `data-testid` and `performance.mark()` usage.

```ts
// ========================================
// File: src/services/api-client.ts
// ========================================
export type Review = {
  rating: number;
};

export type Product = {
  id: string;
  name: string;
  price: number;
  category?: string;
  image?: string;
  reviews?: Review[];
};

type FetchJsonOptions = {
  revalidate?: number;
  cache?: RequestCache;
  tags?: string[];
  timeoutMs?: number;
};

const API_BASE_URL = process.env.EXTERNAL_API_URL;

async function fetchJson<T>(
  path: string,
  options: FetchJsonOptions = {}
): Promise<T> {
  if (!API_BASE_URL) {
    throw new Error("EXTERNAL_API_URL is not configured");
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 10000);

  try {
    const res = await fetch(new URL(path, API_BASE_URL).toString(), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: options.cache,
      signal: controller.signal,
      next: options.revalidate
        ? { revalidate: options.revalidate, tags: options.tags }
        : options.tags
        ? { tags: options.tags }
        : undefined,
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`Upstream request failed: ${res.status} ${body}`);
    }

    return (await res.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

export async function getProducts(): Promise<Product[]> {
  return fetchJson<Product[]>("/products", {
    revalidate: 60,
    tags: ["products"],
  });
}

export async function getUserProfile<T = { id?: string; name?: string }>() {
  return fetchJson<T>("/user/profile", {
    cache: "no-store",
  });
}

export async function sendAnalytics(payload: Record<string, unknown>) {
  if (!API_BASE_URL) return;

  try {
    const res = await fetch(new URL("/analytics", API_BASE_URL).toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`Analytics failed: ${res.status} ${body}`);
    }
  } catch (error) {
    console.error("Analytics request failed", error);
  }
}

// ========================================
// File: src/lib/non-blocking.ts
// ========================================
export function runInBackground(task: Promise<unknown>, label: string) {
  void task.catch((error) => {
    console.error(`Background task failed: ${label}`, error);
  });
}

// ========================================
// File: src/lib/stats.ts
// ========================================
export function sum(values: number[]): number {
  return values.reduce((acc, value) => acc + value, 0);
}

export function mean(values: number[]): number {
  return values.length ? sum(values) / values.length : 0;
}

export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// ========================================
// File: src/app/api/products/route.ts
// ========================================
import { NextResponse } from "next/server";
import {
  getProducts,
  getUserProfile,
  sendAnalytics,
} from "@/services/api-client";
import { runInBackground } from "@/lib/non-blocking";

export async function GET() {
  try {
    const [products, user] = await Promise.all([
      getProducts(),
      getUserProfile<{ id?: string; name?: string; segment?: string }>().catch(
        () => null
      ),
    ]);

    runInBackground(
      sendAnalytics({
        event: "products_api_view",
        userId: user?.id ?? null,
        productCount: products.length,
        ts: Date.now(),
      }),
      "products_api_view"
    );

    return NextResponse.json(
      { products, user },
      {
        status: 200,
        headers: {
          "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300",
        },
      }
    );
  } catch (error) {
    console.error("GET /api/products failed", error);
    return NextResponse.json(
      { error: "Failed to load products" },
      { status: 500 }
    );
  }
}

// ========================================
// File: src/components/ProductCard.tsx
// ========================================
"use client";

import React, { memo, useCallback } from "react";
import type { Product } from "@/services/api-client";

type ProductCardProps = {
  product: Product;
  reviewCount: number;
  inCart: boolean;
  onAddToCart: (productId: string) => void;
};

function ProductCardComponent({
  product,
  reviewCount,
  inCart,
  onAddToCart,
}: ProductCardProps) {
  performance.mark(`ProductCard-render-${product.id}`);

  const handleClick = useCallback(() => {
    onAddToCart(product.id);
  }, [onAddToCart, product.id]);

  return (
    <article
      className="rounded border p-4 shadow-sm"
      data-testid={`product-card-${product.id}`}
    >
      <h2 className="font-semibold">{product.name}</h2>
      <p className="text-sm text-gray-600">{product.category ?? "General"}</p>
      <p className="mt-2 text-lg">${product.price.toFixed(2)}</p>
      <p className="text-sm">{reviewCount} reviews</p>

      <button
        className="mt-3 rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50"
        data-testid={`add-to-cart-${product.id}`}
        disabled={inCart}
        onClick={handleClick}
      >
        {inCart ? "In Cart" : "Add to Cart"}
      </button>
    </article>
  );
}

const ProductCard = memo(
  ProductCardComponent,
  (prev, next) =>
    prev.product === next.product &&
    prev.reviewCount === next.reviewCount &&
    prev.inCart === next.inCart &&
    prev.onAddToCart === next.onAddToCart
);

export default ProductCard;

// ========================================
// File: src/components/ProductList.tsx
// ========================================
"use client";

import { useCallback, useMemo, useState } from "react";
import ProductCard from "@/components/ProductCard";
import type { Product } from "@/services/api-client";

type Props = {
  products: Product[];
};

export default function ProductList({ products }: Props) {
  const [cartIds, setCartIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");

  const cartIdSet = useMemo(() => new Set(cartIds), [cartIds]);

  const reviewCountById = useMemo(() => {
    const map = new Map<string, number>();
    for (const product of products) {
      map.set(product.id, product.reviews?.length ?? 0);
    }
    return map;
  }, [products]);

  const visibleProducts = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return products;

    return products.filter((product) =>
      `${product.name} ${product.category ?? ""}`
        .toLowerCase()
        .includes(normalized)
    );
  }, [products, query]);

  const handleAddToCart = useCallback((productId: string) => {
    setCartIds((prev) => (prev.includes(productId) ? prev : [...prev, productId]));
  }, []);

  return (
    <section className="space-y-4">
      <input
        className="w-full rounded border px-3 py-2"
        placeholder="Search products"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {visibleProducts.map((product) => (
          <ProductCard
            key={product.id}
            product={product}
            reviewCount={reviewCountById.get(product.id) ?? 0}
            inCart={cartIdSet.has(product.id)}
            onAddToCart={handleAddToCart}
          />
        ))}
      </div>
    </section>
  );
}

// ========================================
// File: src/app/page.tsx
// ========================================
import ProductList from "@/components/ProductList";
import { getProducts, getUserProfile } from "@/services/api-client";

export default async function HomePage() {
  const [user, products] = await Promise.all([
    getUserProfile<{ name?: string }>().catch(() => null),
    getProducts(),
  ]);

  return (
    <main className="mx-auto max-w-6xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Storefront</h1>
        {user?.name ? (
          <p className="text-gray-600">Welcome back, {user.name}</p>
        ) : null}
      </header>

      <ProductList products={products} />
    </main>
  );
}

// ========================================
// File: src/app/compare/page.tsx
// ========================================
"use client";

import { useMemo, useState } from "react";
import type { Product } from "@/services/api-client";
import { formatPercent, mean } from "@/lib/stats";

function ComparisonTable({ products }: { products: Product[] }) {
  return (
    <div className="overflow-auto">
      <table className="min-w-full border-collapse border">
        <thead>
          <tr>
            <th className="border p-2 text-left">Product</th>
            <th className="border p-2 text-left">Price</th>
            <th className="border p-2 text-left">Reviews</th>
          </tr>
        </thead>
        <tbody>
          {products.map((product) => (
            <tr key={product.id}>
              <td className="border p-2">{product.name}</td>
              <td className="border p-2">${product.price.toFixed(2)}</td>
              <td className="border p-2">{product.reviews?.length ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AdvancedAnalysis({ products }: { products: Product[] }) {
  const metrics = useMemo(() => {
    const prices = products.map((p) => p.price ?? 0);
    const avgPrice = mean(prices);
    const ratings = products.map((p) =>
      mean((p.reviews ?? []).map((review) => review.rating ?? 0))
    );
    const avgRating = mean(ratings);
    const expensiveShare =
      products.length === 0
        ? 0
        : products.filter((p) => (p.price ?? 0) > avgPrice).length / products.length;

    return { avgPrice, avgRating, expensiveShare };
  }, [products]);

  return (
    <section
      className="rounded border p-4"
      data-testid="advanced-content"
    >
      <h2 className="mb-3 text-lg font-semibold">Advanced Analysis</h2>
      <ul className="space-y-2">
        <li>Average price: ${metrics.avgPrice.toFixed(2)}</li>
        <li>Average rating: {metrics.avgRating.toFixed(2)}</li>
        <li>Premium share: {formatPercent(metrics.expensiveShare)}</li>
      </ul>
    </section>
  );
}

export default function ComparePage() {
  const [activeTab, setActiveTab] = useState<"overview" | "advanced">("overview");

  // Replace with actual selected/compared product source in the target app.
  const selectedProducts: Product[] = [];

  return (
    <main className="mx-auto max-w-6xl p-6">
      <h1 className="mb-4 text-2xl font-bold">Compare Products</h1>

      <div className="mb-6 border-b">
        <nav className="flex gap-4">
          <button
            data-testid="tab-overview"
            onClick={() => setActiveTab("overview")}
            className="border-b-2 px-4 py-3"
          >
            Overview
          </button>
          <button
            data-testid="tab-advanced"
            onClick={() => setActiveTab("advanced")}
            className="border-b-2 px-4 py-3"
          >
            Advanced Analysis
          </button>
        </nav>
      </div>

      {activeTab === "overview" ? (
        <ComparisonTable products={selectedProducts} />
      ) : (
        <AdvancedAnalysis products={selectedProducts} />
      )}
    </main>
  );
}

// ========================================
// File: verify-flows.js
// ========================================
const { execSync, spawn } = require("node:child_process");
const http = require("node:http");

function waitForServer(url, timeoutMs = 30000) {
  const start = Date.now();

  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });

      req.on("error", () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`));
          return;
        }
        setTimeout(attempt, 500);
      });
    };

    attempt();
  });
}

async function main() {
  execSync("npm run build", { stdio: "inherit" });

  const server = spawn("npm", ["run", "start", "--", "--port", "3000"], {
    stdio: "inherit",
    shell: true,
    env: process.env,
  });

  try {
    await waitForServer("http://127.0.0.1:3000");

    const homepage = execSync("curl -fsSL http://127.0.0.1:3000/", {
      encoding: "utf8",
    });
    if (!homepage.includes("Storefront")) {
      throw new Error("Homepage did not render expected content");
    }

    const productsApi = execSync("curl -fsSL http://127.0.0.1:3000/api/products", {
      encoding: "utf8",
    });
    if (!productsApi.includes("products")) {
      throw new Error("/api/products did not return expected JSON shape");
    }

    const comparePage = execSync("curl -fsSL http://127.0.0.1:3000/compare", {
      encoding: "utf8",
    });
    if (!comparePage.includes("tab-advanced")) {
      throw new Error("Compare page missing advanced tab");
    }

    console.log("Verification passed");
  } finally {
    server.kill("SIGTERM");
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
```

---

## Final Pre-Submission Checklist

Before finalizing, confirm all of the following:

- [ ] Homepage still shows product data
- [ ] Add-to-cart still works
- [ ] Compare advanced tab still renders
- [ ] `data-testid="advanced-content"` is preserved
- [ ] No `data-testid` attributes were removed or renamed
- [ ] No `performance.mark()` calls were removed from `ProductCard`
- [ ] External API calls still happen through the real upstream path
- [ ] Route handlers no longer block on analytics/logging
- [ ] Independent server fetches are parallelized
- [ ] Heavy client-side libraries were removed or deferred where reasonable
- [ ] Product grid rerenders were reduced via memoization/stable props
- [ ] `npm run build` passes
- [ ] Production smoke checks pass

If you follow this sequence, you will usually find the right execution path early: **preserve required behavior, verify real upstream usage, parallelize I/O, make side effects non-blocking, reduce client work, and validate in production mode.**