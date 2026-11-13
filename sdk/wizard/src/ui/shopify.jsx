// "Connect your Shopify catalog": three fields in the terminal (the secret masked), sent straight
// to the LIKYLY API by connectShopifyCatalog. The assistant never sees them.

import { useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import TextInput from "ink-text-input";
import { C } from "./theme.jsx";
import { connectShopifyCatalog, normalizeShopDomain, shopifyCredentials } from "../shopify.mjs";

const LATER = "npx @likyly/wizard shopify";

function Field({ label, hint, placeholder, mask, error, onSubmit }) {
  const [value, setValue] = useState("");
  return (
    <Box flexDirection="column">
      <Text>{label}</Text>
      {hint ? <Text color={C.muted}>{hint}</Text> : null}
      <Box marginTop={1} gap={1}>
        <Text color={error ? C.coral : C.blue} bold>
          ›
        </Text>
        <TextInput value={value} onChange={setValue} placeholder={placeholder} mask={mask} onSubmit={() => onSubmit(value.trim())} />
      </Box>
      {error ? <Text color={C.coral}>✗ {error}</Text> : null}
    </Box>
  );
}

export function ShopifyCatalogStep({ apiKey, onDone }) {
  const [stage, setStage] = useState("shop");
  const [shop, setShop] = useState(null);
  const [first, setFirst] = useState("");
  const [error, setError] = useState(null);
  const [credentials, setCredentials] = useState(null);
  const skip = () => onDone([{ level: "info", text: "Skipped. Connect the catalog later:", hint: LATER }], "skipped");

  useEffect(() => {
    if (stage !== "running") return;
    connectShopifyCatalog({ apiKey, shop, credentials }).then((results) => onDone(results, results.some((r) => r.level === "fail") ? "not connected" : shop));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once, when the fields are filled
  }, [stage]);

  // No developer key (sign-in skipped): LIKYLY cannot be asked to create the source.
  useEffect(() => {
    if (!apiKey) onDone([{ level: "warn", text: "No developer key: sign in first, then connect the catalog:", hint: LATER }], "skipped");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once
  }, []);
  if (!apiKey) return null;
  if (stage === "shop")
    return (
      <Field
        key="shop"
        label="Your store's address"
        hint="The credentials go straight to LIKYLY, encrypted: not in a file, not to your assistant. Enter on an empty field skips."
        placeholder="your-store.myshopify.com"
        error={error}
        onSubmit={(v) => {
          if (!v) return skip();
          const domain = normalizeShopDomain(v);
          if (!domain) return setError("Expected your-store.myshopify.com (the store's Shopify address, not its own domain)");
          setError(null);
          setShop(domain);
          setStage("id");
        }}
      />
    );
  if (stage === "id")
    return (
      <Field
        key="id"
        label={`Client ID of an app installed on ${shop} with read_products`}
        hint="Shopify Dev Dashboard → your app → Settings. On an older store, an Admin API token (shpat_…) works too."
        placeholder="Client ID, or shpat_…"
        error={error}
        onSubmit={(v) => {
          if (!v) return skip();
          setError(null);
          if (v.startsWith("shpat_")) {
            setCredentials(shopifyCredentials(v));
            return setStage("running");
          }
          setFirst(v);
          setStage("secret");
        }}
      />
    );
  if (stage === "secret")
    return (
      <Field
        key="secret"
        label="Client secret"
        hint="Same page, under the Client ID. It stays hidden as you paste it."
        placeholder="shpss_…"
        mask="•"
        error={error}
        onSubmit={(v) => {
          if (!v) return skip();
          setCredentials(shopifyCredentials(first, v));
          setStage("running");
        }}
      />
    );
  return (
    <Text>
      <Text color={C.blue}>
        <Spinner type="dots" />
      </Text>{" "}
      Connecting {shop} to LIKYLY, then starting the first sync…
    </Text>
  );
}
