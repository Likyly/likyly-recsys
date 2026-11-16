// "Connect your Magento catalog". By default nothing is typed and nothing is done in the admin: the
// wizard runs bin/magento itself (the way this project runs it: DDEV, Warden, Docker Compose or
// PHP), enables the LIKYLY module and creates a read-only Integration with the module's
// `likyly:integration` command; the credentials go straight to the LIKYLY API, the assistant never
// sees them. The other way, kept for stores where bin/magento can't run from here: the store's
// address and an Integration's four values, typed (the secrets masked).

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { useEffect, useRef, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { C } from "./theme.jsx";
import { Select } from "./widgets.jsx";
import { Field } from "./shopify.jsx";
import {
  connectMagentoCatalog,
  createMagentoIntegration,
  detectMagentoCli,
  magentoCredentials,
  normalizeStoreUrl,
  splitCommand,
} from "../magento.mjs";
import { MAGENTO_MODULE } from "../frameworks.mjs";
import { commandExists, runAsync } from "../setup.mjs";
import { describeSource } from "../catalog.mjs";
import { Checking, ExistingSource, Intro, useConnectedSource } from "./catalog.jsx";

const AUTO_INTRO = [
  "LIKYLY recommends from your store's products: it reads them through the store's REST API, with a read-only Integration.",
  "Nothing to do in the Magento admin: the wizard enables the LIKYLY module and creates that Integration with bin/magento.",
];
const MANUAL_INTRO = [
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

/** app/etc/config.php lists the module as enabled. */
const moduleEnabled = (dir) => {
  try {
    return /['"]Likyly_Recommendations['"]\s*=>\s*1/.test(readFileSync(join(dir, "app/etc/config.php"), "utf8"));
  } catch {
    return false;
  }
};

/** The wizard's module is in the project, with its likyly:integration command. */
const hasModuleCommand = (dir) => existsSync(join(dir, MAGENTO_MODULE, "registration.php")) && existsSync(join(dir, MAGENTO_MODULE, "Console/Command/IntegrationCommand.php"));

export function MagentoCatalogStep({ apiKey, dir, onDone, standalone = false }) {
  const [stage, setStage] = useState("check");
  const [existing, setExisting] = useState(null); // the store already connected, if any
  const [storeUrl, setStoreUrl] = useState(null);
  const [values, setValues] = useState({});
  const [error, setError] = useState(null);
  const [refusal, setRefusal] = useState(null);
  const [fetched, setFetched] = useState(0);
  const [cli, setCli] = useState(() => (dir ? detectMagentoCli(dir, { commandExists }) : null));
  const [progress, setProgress] = useState({ step: "", line: "" });
  const auto = useRef(null); // { storeCode, credentials } from likyly:integration
  const canRun = Boolean(dir) && hasModuleCommand(dir);
  const skip = () => onDone([{ level: "info", text: "Skipped. Connect the catalog later:", hint: LATER }], "skipped");
  const firstStage = canRun ? "how" : "store";

  // A store already connected, kept: only enable the module (no new Integration).
  useEffect(() => {
    if (stage !== "enabling") return;
    const label = (existing.config?.store_url ?? existing.name).replace(/^https?:\/\//, "");
    const kept = { level: "ok", text: `Already connected: ${describeSource(label, existing)}` };
    createMagentoIntegration({
      dir,
      cli: cli.cmd,
      integration: false,
      run: (cmd, onLine) => runAsync(dir, cmd, onLine),
      onStep: (step) => setProgress({ step, line: "" }),
      onLine: (line) => setProgress((p) => ({ ...p, line: line.trim().slice(0, 100) })),
    }).then((r) =>
      onDone(
        r.ok
          ? [kept, { level: "ok", text: "LIKYLY module enabled", hint: `(${cli.label})` }]
          : [kept, { level: "warn", text: `The LIKYLY module is not enabled: ${r.error}. Run:`, hint: "bin/magento module:enable Likyly_Recommendations && bin/magento setup:upgrade" }],
        label,
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once
  }, [stage]);

  // bin/magento: enable the module, create the Integration.
  useEffect(() => {
    if (stage !== "creating") return;
    createMagentoIntegration({
      dir,
      cli: cli.cmd,
      run: (cmd, onLine) => runAsync(dir, cmd, onLine),
      onStep: (step) => setProgress({ step, line: "" }),
      onLine: (line) => setProgress((p) => ({ ...p, line: line.trim().slice(0, 100) })),
    }).then((r) => {
      if (!r.ok) {
        setError(r.error);
        return setStage("how");
      }
      auto.current = { storeCode: r.storeCode, credentials: r.credentials };
      setStoreUrl(r.storeUrl);
      setStage("running");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per attempt
  }, [stage]);

  useEffect(() => {
    if (stage !== "running") return;
    const credentials = auto.current?.credentials ?? magentoCredentials(values);
    connectMagentoCatalog({ apiKey, storeUrl, storeCode: auto.current?.storeCode, credentials, onProgress: setFetched }).then((results) => {
      const refused = results.find((r) => r.retry);
      if (refused) {
        const wasAuto = Boolean(auto.current);
        auto.current = null;
        setValues({});
        setFetched(0);
        setRefusal(`${refused.text.replace(/^Magento refused the connection: /, "")}.${wasAuto ? "" : " Check the address and the Integration's values, or leave empty to skip."}`);
        return setStage(wasAuto ? "how" : "store");
      }
      const head = auto.current ? [{ level: "ok", text: "LIKYLY module enabled, read-only Integration \"LIKYLY\" created", hint: `(${cli.label})` }] : [];
      onDone([...head, ...results], results.some((r) => r.level === "fail") ? "not connected" : storeUrl.replace(/^https?:\/\//, ""));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once, when the store and credentials are known
  }, [stage]);

  useConnectedSource({
    apiKey,
    type: "magento",
    onResult: (source) => {
      setExisting(source);
      setStage(source ? "existing" : firstStage);
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
          if (choice === "keep") {
            if (canRun && cli && !moduleEnabled(dir)) return setStage("enabling");
            return onDone([{ level: "ok", text: `Already connected: ${describeSource(label, existing)}` }], label);
          }
          setValues({});
          if (canRun) return setStage("how");
          setStoreUrl(choice === "credentials" ? existing.config?.store_url ?? null : null);
          setStage(choice === "credentials" && existing.config?.store_url ? 0 : "store");
        }}
      />
    );
  }

  if (stage === "how") {
    const items = [
      ...(cli ? [{ value: "auto", label: "Set it up for me (recommended)" }] : []),
      { value: "command", label: cli ? "Run bin/magento another way" : "Tell me how to run bin/magento" },
      { value: "manual", label: "I'll give an Integration's values myself" },
      { value: "skip", label: "Skip for now" },
    ];
    return (
      <Box flexDirection="column">
        {existing ? null : <Intro lines={AUTO_INTRO} />}
        <Text color={C.muted}>
          {cli ? `bin/magento runs with: ${cli.label}` : "bin/magento: not found from here (no DDEV, Warden, Docker Compose service or php)"}
        </Text>
        {error ? <Text color={C.coral}>✗ {error}</Text> : null}
        {refusal ? <Text color={C.coral}>✗ {refusal}</Text> : null}
        <Select
          items={items}
          onSubmit={(choice) => {
            setError(null);
            if (choice === "skip") return skip();
            if (choice === "manual") {
              setRefusal(null);
              return setStage("store");
            }
            if (choice === "command") return setStage("command");
            setRefusal(null);
            setStage("creating");
          }}
        />
      </Box>
    );
  }

  if (stage === "command")
    return (
      <Field
        key="command"
        label="How bin/magento runs in this project"
        hint="The whole command, as you would type it before setup:upgrade - e.g. docker compose exec -T -u www-data php bin/magento. The wizard runs module:enable, setup:upgrade, cache:flush and likyly:integration with it."
        placeholder="bin/magento"
        initial={cli ? cli.cmd.join(" ") : "php bin/magento"}
        onSubmit={(v) => {
          const cmd = splitCommand(v);
          if (!cmd.length) return setStage("how");
          setCli({ label: v.trim(), cmd });
          setRefusal(null);
          setStage("creating");
        }}
      />
    );

  if (stage === "creating" || stage === "enabling")
    return (
      <Box flexDirection="column">
        <Text>
          <Text color={C.blue}>
            <Spinner type="dots" />
          </Text>{" "}
          {progress.step || "Running bin/magento"}…
        </Text>
        {progress.line ? <Text color={C.muted}>  {progress.line}</Text> : null}
      </Box>
    );

  if (stage === "store")
    return (
      <>
        {existing ? null : <Intro lines={MANUAL_INTRO} />}
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
