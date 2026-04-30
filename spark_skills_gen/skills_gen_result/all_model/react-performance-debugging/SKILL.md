---
name: React/Next.js Performance Debugging
version: 1.0.0
category: react-performance-debugging
tags: [react, nextjs, performance, optimization, bundle-size, rendering, api]
description: Diagnose and fix performance issues in Next.js apps — covering API route optimization, bundle size reduction, and excessive re-rendering.
---

## Module 1: Reconnaissance First

Before touching any code, map the codebase and understand what the tests expect.

### Step 1: Explore the project structure

```bash
find /app/src -type f | sort
find / -name "test_performance.py" 2>/dev/null | head -5
```

Read the test file first — it tells you the exact endpoints, payloads, and response time thresholds you must satisfy.

### Step 2: Read all relevant source files before writing anything

Use `readCode` or `readFile` on:
- All API route handlers (`/app/src/app/api/**/*.ts`)
- Page components that are slow (`page.tsx` files)
- Shared components that render frequently (`ProductCard`, `ProductList`, etc.)
- Any component importing large third-party libraries

Never assume file structure. A failed build from writing to the wrong path blocks all tests.

---

## Module 2: The Three Performance Fix Patterns

### Pattern A — Parallelize Sequential API Awaits

Sequential `await` calls in API routes are the most common cause of slow responses.

```ts
// BEFORE (slow — sequential, ~1200ms)
const user = await fetchUser(id);
const products = await fetchProducts();
const reviews = await fetchReviews();

// AFTER (fast — parallel, ~400ms)
const [user, products, reviews] = await Promise.all([
  fetchUser(id),
  fetchProducts(),
  fetchReviews(),
]);
```

Also fire non-critical side effects (analytics, logging) without `await`:

```ts
// Don't block the response on analytics
logAnalyticsEvent(payload).catch(() => {}); // fire-and-forget
const data = await fetchCoreData();
return Response.json(data);
```

### Pattern B — Lazy-Load Heavy Dependencies

Large libraries (e.g. `mathjs`, `chart.js`, `lodash`) imported at the top of a page inflate the initial bundle. Use `next/dynamic` to defer them until needed.

```ts
// compare/page.tsx
import dynamic from 'next/dynamic';

const AdvancedAnalysis = dynamic(
  () => import('@/components/AdvancedAnalysis'),
  { ssr: false, loading: () => <p>Loading...</p> }
);

// Only render when the user activates the tab
{activeTab === 'advanced' && <AdvancedAnalysis products={products} />}
```

Extract the heavy component into its own file so the dynamic boundary is clean.

### Pattern C — Prevent Excessive React Re-renders

Three tools, applied together:

```ts
// 1. Memoize expensive derived values
const filteredProducts = useMemo(
  () => products.filter(p => p.category === selected),
  [products, selected]
);

// 2. Stabilize callbacks passed as props
const handleAddToCart = useCallback((id: string) => {
  dispatch({ type: 'ADD', id });
}, [dispatch]);

// 3. Wrap list-item components in React.memo
export default React.memo(function ProductCard({ product, onAdd }) {
  // only re-renders when product or onAdd reference changes
});
```

Replace O(n²) patterns inside render with a pre-built map:

```ts
// BEFORE — O(n²): called per product, iterates all reviews each time
function getReviewCount(productId) {
  return reviews.filter(r => r.productId === productId).length;
}

// AFTER — O(n) once, O(1) lookup
const reviewCountMap = useMemo(() => {
  const map: Record<string, number> = {};
  for (const r of reviews) map[r.productId] = (map[r.productId] ?? 0) + 1;
  return map;
}, [reviews]);
```

---

## Module 3: Verification Before Finalizing

1. Confirm the build compiles without errors — tests cannot run if the build fails.
2. Check that `data-testid` attributes are untouched (tests query by them).
3. Confirm `performance.mark()` calls in `ProductCard` are still present.
4. Verify the compare page's Advanced tab still renders its content (don't remove the component, just lazy-load it).
5. Hit the checkout/cart API route manually and confirm it responds within the test's time threshold.

---

## Common Pitfalls

- Writing files before reading them — always inspect actual file contents first; assumed paths cause build failures that block all tests.
- Removing components to "simplify" — lazy-loading is not the same as deleting; the component must still render when its tab/section is active.
- Forgetting `data-testid` constraints — never rename or remove attributes used by tests, even during refactors.
- Leaving `performance.mark()` calls out — they are required by the task constraints; `React.memo` wrapping must not strip them.
- Parallelizing calls that have data dependencies — only use `Promise.all` when the calls are truly independent.
- Dynamic import with `ssr: true` on a component that uses browser APIs — set `ssr: false` for client-only heavy libs.
- Fire-and-forget without `.catch()` — unhandled promise rejections can crash the route in some Node versions; always add `.catch(() => {})`.
