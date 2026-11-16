// "Connect your Magento catalog": the store's address, then the four values of an Integration
// (the two secrets masked), sent straight to the LIKYLY API by connectMagentoCatalog. The
// assistant never sees them.

import { useEffect, useState } from "react";
import { Text } from "ink";
import Spinner from "ink-spinner";
import { C } from "./theme.jsx";
import { Field } from "./shopify.jsx";
import { connectMagentoCatalog, magentoCredentials, normalizeStoreUrl } from "../magento.mjs";
import { describeSource } from "../catalog.mjs";
import { Checking, ExistingSource, Intro, useConnectedSource } from "./catalog.jsx";

const INTRO = [
  "LIKYLY recommends from your store's products: it reads them through the store's REST API, with an Integration.",
  "You'll give 1. the store's address · 2.–5. the Integration's Consumer Key, Consumer Secret, Access Token and Access Token Secret",
  "Magento admin → System → Extensions → Integrations: add one with access to Catalog (Products, Categories), Stores and Inventory, then Activate.",
];

const LATER = "npx @likyly/wizard magento";
const WHERE = "Magento admin → System → Extensions → Integrations → your integration → Integration Info.";

// The four values, in the order Magento lists them.
const FIELDS = [
  ["consumerKey", "Consumer Key", false],
  ["consumerSecret", "Consumer Secret", true],
  ["accessToken", "Access Token", false],
  ["accessTokenSecret", "Access Token Secret", true],
];

export function MagentoCatalogStep({ apiKey, onDone, standalone = false }) {
  const [stage, setStage] = useState("check");
  const [existing, setExisting] = useState(null); // the store already connected, if any
  const [storeUrl, setStoreUrl] = useState(null);
  const [values, setValues] = useState({});
  const [error, setError] = useState(null);
  const [refusal, setRefusal] = useState(null);
  const [fetched, setFetched] = useState(0);
  const skip = () => onDone([{ level: "info", text: "Skipped. Connect the catalog later:", hint: LATER }], "skipped");

  useEffect(() => {
    if (stage !== "running") return;
    connectMagentoCatalog({ apiKey, storeUrl, credentials: magentoCredentials(values), onProgress: setFetched }).then((results) => {
      const refused = results.find((r) => r.retry);
      if (refused) {
        setValues({});
        setFetched(0);
        setRefusal(`${refused.text.replace(/^Magento refused the connection: /, "")}. Check the address and the Integration's values, or leave empty to skip.`);
        return setStage("store");
      }
      onDone(results, results.some((r) => r.level === "fail") ? "not connected" : storeUrl.replace(/^https?:\/\//, ""));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once, when the fields are filled
  }, [stage]);

  useConnectedSource({
    apiKey,
    type: "magento",
    onResult: (source) => {
      setExisting(source);
      setStage(source ? "existing" : "store");
    },
  });

  useEffect(() => {
    if (!apiKey) onDone([{ level: "warn", text: "No developer key: sign in first, then connect the catalog:", hint: LATER }], "skipped");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once
  }, []);
  if (!apiKey) return null;
  if (stage === "check") return <Checking platform="Magento" />;
  if (stage === "existing") {
    const label = (existing.config?.store_url ?? existing.name).replace(/^https?:\/\//, "");
    return (
      <ExistingSource
        platform="Magento"
        label={label}
        source={existing}
        standalone={standalone}
        onChoice={(choice) => {
          if (choice === "keep") return onDone([{ level: "ok", text: `Already connected: ${describeSource(label, existing)}` }], label);
          setValues({});
          setStoreUrl(choice === "credentials" ? existing.config?.store_url ?? null : null);
          setStage(choice === "credentials" && existing.config?.store_url ? 0 : "store");
        }}
      />
    );
  }

  if (stage === "store")
    return (
      <>
      {existing ? null : <Intro lines={INTRO} />}
      <Field
        key="store"
        label="Your store's address"
        hint="The credentials go straight to LIKYLY, encrypted: not in a file, not to your assistant. Enter on an empty field skips."
        placeholder="https://shop.example.com"
        initial={storeUrl ?? ""}
        error={error ?? refusal}
        onSubmit={(v) => {
          if (!v) return skip();
          const url = normalizeStoreUrl(v);
          if (!url) return setError("Expected the store's address, like https://shop.example.com");
          setError(null);
          setStoreUrl(url);
          setStage(0);
        }}
      />
      </>
    );
  if (typeof stage === "number") {
    const [name, label, secret] = FIELDS[stage];
    return (
      <Field
        key={name}
        label={`${label} of an active Integration`}
        hint={
          stage === 0
            ? `${WHERE} No integration yet: Add New Integration, API → Resource Access: Catalog (Products, Categories), Stores → All Stores and Inventory; Save, then Activate.`
            : secret
              ? "Same page. It stays hidden as you paste it."
              : "Same page."
        }
        placeholder={label}
        mask={secret ? "•" : undefined}
        error={stage === 0 ? (error ?? refusal) : error}
        onSubmit={(v) => {
          if (!v) return skip();
          setRefusal(null);
          setValues((prev) => ({ ...prev, [name]: v }));
          setStage(stage + 1 < FIELDS.length ? stage + 1 : "running");
        }}
      />
    );
  }
  return (
    <Text>
      <Text color={C.blue}>
        <Spinner type="dots" />
      </Text>{" "}
      {fetched ? `Syncing ${storeUrl}: ${fetched} product(s) read so far…` : `Connecting ${storeUrl} to LIKYLY and syncing its products (about a minute)…`}
    </Text>
  );
}
