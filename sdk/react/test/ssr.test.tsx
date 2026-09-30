import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, it } from "node:test";
import { LikylyProvider, LikylyRecommendations, useRecommendations } from "../src/index.js";

// Regression test for a real bug found while integrating @likyly/react into a Next.js app
// (examples/demo-storefront): `useSyncExternalStore` without a third (getServerSnapshot)
// argument throws "Missing getServerSnapshot" the moment a page using this hook is
// server-rendered/statically generated - `next dev` alone never catches this, since it only
// ever renders these components client-side; `next build`'s static generation does.
describe("server-side rendering", () => {
  it("useRecommendations does not throw during a server render", () => {
    function Probe() {
      const { isLoading, items } = useRecommendations({ placement: "pdp-related", context: { itemId: "SKU-1" } });
      return <div>{`${isLoading} ${items.length}`}</div>;
    }
    const html = renderToStaticMarkup(
      <LikylyProvider apiKey="pk_test_ssr">
        <Probe />
      </LikylyProvider>,
    );
    assert.match(html, /<div>/);
  });

  it("<LikylyRecommendations> does not throw during a server render", () => {
    const html = renderToStaticMarkup(
      <LikylyProvider apiKey="pk_test_ssr">
        <LikylyRecommendations placement="pdp-related" itemId="SKU-1" />
      </LikylyProvider>,
    );
    assert.equal(typeof html, "string"); // isLoading, 0 items on first render -> renders its loadingFallback (null)
  });
});
