# @likyly/react

React bindings for [`@likyly/sdk`](../js): `useRecommendations` (headless) and
`<LikylyRecommendations>` (ready-to-use) for rendering a LIKYLY [placement](../../docs/placements.md)
and tracking the impression/click events it needs to learn from.

## Why a separate package (not React support inside `@likyly/sdk`)

- `@likyly/sdk` is dependency-free and runs in Node, edge runtimes and browsers alike (it's used
  server-side for `items`/`users` too). Adding React to it would force that dependency - and the
  `"use client"` boundary Next.js's App Router needs - onto every consumer, including
  server-side-only integrations that never render anything.
- This repo's own convention is one package per surface (`sdk/js`, `sdk/python`, `sdk/php`,
  `sdk/mcp`, ...) - `@likyly/react` is that same pattern applied to a second JS surface, not a
  new idea.
- It matches how this kind of binding is normally split in the ecosystem (a core client plus a
  thin framework package: `stripe` / `@stripe/react-stripe-js`, `algoliasearch` /
  `react-instantsearch`).
- `@likyly/react` depends on `@likyly/sdk` and adds **zero** wire/HTTP logic of its own - every
  method here calls straight through to the `Likyly` client underneath (see `src/controller.ts`,
  the plain-JS state machine both `useRecommendations` and `<LikylyRecommendations>` share - it
  has no React import at all, which is also why it's unit-tested with no DOM/renderer needed).

## Install

```sh
npm install @likyly/react @likyly/sdk react
```

> During local development in the `likyly-recsys` monorepo (before `@likyly/sdk` is published
> to npm), this package's `dependencies` point at it via a relative `file:../js` reference.
> Once published, that becomes a normal semver range - nothing a consumer needs to think about.
> One consequence in the meantime: npm installs a `file:` dependency as a symlink pointing
> outside the consuming app's own folder, which **Turbopack fails to resolve** ("Module not
> found"). Run `next dev`/`next build` with `--webpack` until both packages are on npm - see
> `examples/demo-storefront` for a working setup with this already applied.

## Quick start

```tsx
import { LikylyProvider, LikylyRecommendations } from "@likyly/react";

export function App() {
  return (
    <LikylyProvider apiKey={process.env.NEXT_PUBLIC_LIKYLY_KEY!}>
      <ProductPage />
    </LikylyProvider>
  );
}

function ProductPage({ product, user, sessionId }) {
  return (
    <LikylyRecommendations
      placement="pdp-related"
      itemId={product.id}
      userId={user?.id}
      sessionId={sessionId}
      limit={4}
    />
  );
}
```

`apiKey` must be your **restricted public key** - this runs in the browser. `Likyly`'s own
constructor (from `@likyly/sdk`) refuses a secret- or developer-shaped key when it detects it's
running in one, so passing the wrong key here fails loudly and immediately rather than shipping
quietly. See [Security](#security) below.

Configure the placement itself (`pdp-related` above) once via the LIKYLY MCP admin tools
(`create_placement`) or dashboard - never from this package, which is intentionally
runtime-only. `get_placement_requirements` tells you exactly which of `itemId`/`userId`/
`sessionId`/... a given placement actually needs.

## Level 1: headless (`useRecommendations`)

100% of the markup is yours; the hook fetches and gives you tracking helpers to call yourself:

```tsx
import { useRecommendations } from "@likyly/react";

function RelatedProducts({ product, user, sessionId }) {
  const { items, isLoading, strategyUsed, trackImpression, trackClick } = useRecommendations({
    placement: "pdp-related",
    context: { itemId: product.id, userId: user?.id, sessionId },
    limit: 4,
  });

  if (isLoading) return <Skeleton />;
  return (
    <div className="my-own-grid">
      {items.map((item) => (
        <MyOwnCard
          key={item.itemId}
          item={item}
          onVisible={() => trackImpression(item.itemId)}
          onClick={() => trackClick(item.itemId)}
        />
      ))}
    </div>
  );
}
```

`trackImpression`/`trackClick` are manual here - a headless hook has no DOM for LIKYLY to
observe, so it can't tell what the visitor actually saw. Call `trackImpression` once your own
card is actually visible (e.g. from your own `IntersectionObserver`, or eagerly on render if
that's an acceptable approximation for your UI), and `trackClick` from your card's click handler.

## Level 2: ready-to-use (`<LikylyRecommendations>`)

Fetches, renders, **and auto-tracks** `recommendationImpression` (via `IntersectionObserver` -
fired when a card is actually scrolled into view, not merely fetched) and `recommendationClick`.
Doesn't lock you out of styling it:

```tsx
// Default markup, styled via className / the data-likyly-* attributes it sets:
<LikylyRecommendations placement="pdp-related" itemId={product.id} userId={user?.id} className="related-grid" />

// Full control over each card, while impression/click tracking stays automatic:
<LikylyRecommendations
  placement="pdp-related"
  itemId={product.id}
  userId={user?.id}
  renderItem={(item, { onClick }) => (
    <MyExistingProductCard product={item} onClick={onClick} />
  )}
/>
```

## What's never auto-tracked

`product_view`, `add_to_cart` and `purchase` are **never** fired by this package, on purpose -
LIKYLY has no reliable, universal way to know when your app navigated to a product page, added
something to a cart, or completed a checkout across every possible stack. Instrument these
yourself with the plain SDK helpers, at the point in your own code where each actually happens:

```ts
await likyly.events.productView({ itemId: product.id, userId: user?.id, sessionId });
await likyly.events.addToCart({ itemId: product.id, userId: user?.id, sessionId, quantity: 1 });
await likyly.events.purchase({ eventId: `purchase_${orderId}_${item.id}`, itemId: item.id, userId: user?.id, quantity: item.quantity, properties: { price: item.price, currency: "EUR", orderId } });
```

Call `get_tracking_requirements` (an MCP tool, or `GET /placements/{slug}/tracking-requirements`)
for exactly which events a given placement needs, and `get_placement_health` /
`validate_integration` to confirm they're actually arriving once wired up.

## Anonymous sessions

`@likyly/sdk`'s `LikylySession` (held by `<LikylyProvider>`, reachable via `useLikyly().session`)
is a first-party, cookieless anonymous id - no need to invent your own:

```tsx
import { useLikyly } from "@likyly/react";

function LoginForm() {
  const { client, session } = useLikyly();
  async function onLogin(user) {
    await session.identify(client, user.id); // attaches this session's past interactions to them
  }
}
```

## Security

- `<LikylyProvider apiKey>` must be the **public** key. `@likyly/sdk`'s `Likyly` constructor
  throws immediately if it detects a secret/developer-shaped key (`sk_`/`lk_` prefix) running
  where `window`/`document` exist - i.e. exactly the "this ended up in a browser" mistake.
- Never read that key from a server-only env var, and never name the env var something like
  `NEXT_PUBLIC_LIKYLY_SECRET_KEY` - only the public key belongs in a `NEXT_PUBLIC_*`/
  `VITE_PUBLIC_*`-style variable at all.
- This package has no method that accepts or needs a secret/developer key - there is
  structurally nothing here that would tempt using one. Placement/data-source configuration is
  the LIKYLY MCP admin tools' job (secret/developer key, server-side, from your coding agent),
  never this runtime package's.

## Requirements

React 18 or 19 (`useSyncExternalStore`). Server-rendered frameworks (Next.js, Remix, ...): the
files that use hooks or browser APIs are marked `"use client"` already - use `<LikylyRecommendations>`/
`useRecommendations` from a Client Component.
