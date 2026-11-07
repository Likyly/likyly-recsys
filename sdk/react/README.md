# @likyly/react

Show LIKYLY recommendations in a React app, with the impressions and clicks LIKYLY learns from
tracked for you.

The fastest way to set it up is the wizard, which installs this package, writes your public key
and connects your AI coding assistant:

```sh
npx @likyly/wizard
```

## Install

```sh
npm install @likyly/react
```

React 18 or 19. Works with Next.js (App Router included), Vite, Remix and others.

## Quick start

```tsx
import { LikylyProvider, LikylyRecommendations, useLikyly } from "@likyly/react";

export function App() {
  return (
    <LikylyProvider apiKey={process.env.NEXT_PUBLIC_LIKYLY_KEY!}>
      <ProductPage />
    </LikylyProvider>
  );
}

function ProductPage({ product, user }) {
  const { session } = useLikyly();
  return (
    <LikylyRecommendations placement="pdp-related" itemId={product.id} limit={4}
      userId={user?.id} sessionId={session.getAnonymousId()} />
  );
}
```

- `apiKey` is your **public key** (`pk_…`). A secret or developer key is refused in the browser.
- `pdp-related` is a placement you create once in LIKYLY (console or AI assistant), not in code.
- `sessionId` is the visitor's anonymous id, kept in the browser (first-party, no cookie). Pass it
  so visitors who are not signed in count too.

## Your own markup

Keep your own cards; impressions and clicks are still tracked:

```tsx
<LikylyRecommendations
  placement="pdp-related"
  itemId={product.id}
  renderItem={(item, { onClick }) => <ProductCard product={item} onClick={onClick} />}
/>
```

Or take full control with the hook, and report what was seen and clicked:

```tsx
import { useLikyly, useRecommendations } from "@likyly/react";

function Related({ product }) {
  const { session } = useLikyly();
  const { items, isLoading, trackImpression, trackClick } = useRecommendations({
    placement: "pdp-related",
    context: { itemId: product.id, sessionId: session.getAnonymousId() },
    limit: 4,
  });
  if (isLoading) return null;
  return items.map((item) => (
    <ProductCard key={item.itemId} product={item}
      onVisible={() => trackImpression(item.itemId)} onClick={() => trackClick(item.itemId)} />
  ));
}
```

## Other events

Product views, add-to-cart and purchases happen in your own code, so send them from there:

```ts
const { client } = useLikyly();
await client.events.productView({ itemId: product.id, userId: user?.id });
await client.events.addToCart({ itemId: product.id, userId: user?.id, quantity: 1 });
await client.events.purchase({ eventId: `purchase_${order.id}_${item.id}`, itemId: item.id, userId: user?.id,
  quantity: item.quantity, properties: { price: item.price, currency: "EUR" } });
```

Set `eventId` on purchases: a retried purchase is then never counted twice.

## Sign-in

At sign-in, attach what the visitor did anonymously to their account:

```tsx
const { client, session } = useLikyly();
await session.identify(client, user.id);
```

## Links

- [`@likyly/sdk`](https://www.npmjs.com/package/@likyly/sdk): the JavaScript client this package is built on
- [likyly.com](https://likyly.com)
