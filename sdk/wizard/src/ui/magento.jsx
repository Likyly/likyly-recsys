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
import { MultiSelect, Select } from "./widgets.jsx";
import { openUrl } from "./system.mjs";
import { Field } from "./shopify.jsx";
import {
  magentoCatalogName,
  categoryCounts,
  connectMagentoCatalog,
  createIntegration,
  createMagentoIntegration,
  itemsInCategories,
  prepareMagentoStore,
  productRoom,
  isMagentoPushSource,
  pushMagentoCatalog,
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
  "LIKYLY recommends from your store's products. Nothing to do in the Magento admin: the wizard enables the LIKYLY module with bin/magento, then",
  "· a store online: creates a read-only Integration, and LIKYLY reads the products through the store's REST API;",
  "· a store on this computer (localhost, .test, DDEV…): sends the products to LIKYLY itself, no Integration.",
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
  const auto = useRef(null); // { mode, storeCode, items, categories, credentials }
  const [plan, setPlan] = useState(null); // { room, planName, upgradeUrl } when the store doesn't fit
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

  // bin/magento: enable the module, read the products; then check they fit the plan.
  useEffect(() => {
    if (stage !== "creating") return;
    prepareMagentoStore({
      dir,
      cli: cli.cmd,
      run: (cmd, onLine) => runAsync(dir, cmd, onLine),
      onStep: (step) => setProgress({ step, line: "" }),
      onLine: (line) => setProgress((p) => ({ ...p, line: line.trim().slice(0, 100) })),
    }).then(async (r) => {
      if (!r.ok) {
        setError(r.error);
        return setStage("how");
      }
      auto.current = { mode: r.mode, storeCode: r.storeCode, items: r.items, categories: null };
      setStoreUrl(r.storeUrl);
      setProgress({ step: "Checking your plan", line: "" });
      // Products this catalog already has are sent again, they take no new room.
      const inCatalog = existing && magentoCatalogName(r.storeUrl) === existing.product_type ? (existing.itemCount ?? 0) : 0;
      const room = await productRoom({ apiKey, inCatalog });
      if (room && room.room !== null && r.items.length > room.room) {
        setPlan(room);
        return setStage("plan");
      }
      proceed(null);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per attempt
  }, [stage]);

  /** Go on with every product (categories null) or only those of the chosen categories. */
  const proceed = (categories) => {
    auto.current.categories = categories;
    if (categories) auto.current.items = itemsInCategories(auto.current.items, categories);
    setStage(auto.current.mode === "push" ? "pushing" : "integration");
  };

  // A store LIKYLY reads itself: the read-only Integration it reads it with.
  useEffect(() => {
    if (stage !== "integration") return;
    createIntegration({ cli: cli.cmd, run: (cmd, onLine) => runAsync(dir, cmd, onLine), onStep: (step) => setProgress({ step, line: "" }) }).then((r) => {
      if (!r.ok) {
        setError(r.error);
        return setStage("how");
      }
      auto.current.credentials = r.credentials;
      setStage("running");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once
  }, [stage]);

  const chosenLine = () =>
    auto.current?.categories
      ? [{ level: "info", text: `Only these categories (your plan's room): ${auto.current.categories.map((c) => c ?? "no category").join(", ")}`, hint: `- ${LATER} to change them` }]
      : [];

  useEffect(() => {
    if (stage !== "running") return;
    const credentials = auto.current?.credentials ?? magentoCredentials(values);
    connectMagentoCatalog({ apiKey, storeUrl, storeCode: auto.current?.storeCode, categories: auto.current?.categories, credentials, onProgress: setFetched }).then((results) => {
      const refused = results.find((r) => r.retry);
      if (refused) {
        const wasAuto = Boolean(auto.current);
        auto.current = null;
        setValues({});
        setFetched(0);
        setRefusal(`${refused.text.replace(/^Magento refused the connection: /, "")}.${wasAuto ? "" : " Check the address and the Integration's values, or leave empty to skip."}`);
        return setStage(wasAuto ? "how" : "store");
      }
      const head = auto.current ? [{ level: "ok", text: "LIKYLY module enabled, read-only Integration \"LIKYLY\" created", hint: `(${cli.label})` }, ...chosenLine()] : [];
      onDone([...head, ...results], results.some((r) => r.level === "fail") ? "not connected" : storeUrl.replace(/^https?:\/\//, ""));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once, when the store and credentials are known
  }, [stage]);

  // A store on this computer: the wizard sends its products (LIKYLY's servers can't read it).
  useEffect(() => {
    if (stage !== "pushing") return;
    pushMagentoCatalog({ apiKey, storeUrl, items: auto.current.items, onProgress: setFetched }).then((results) => {
      const failed = results.some((r) => r.level === "fail");
      onDone(
        [
          { level: "ok", text: "LIKYLY module enabled", hint: `(${cli.label})` },
          ...chosenLine(),
          ...results,
          ...(failed
            ? []
            : [
                { level: "info", text: "Your store runs on this computer, out of LIKYLY's reach: the wizard sent its products, no Integration needed. After a catalog change, send it again:", hint: LATER },
              ]),
        ],
        failed ? "not connected" : storeUrl.replace(/^https?:\/\//, ""),
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once
  }, [stage]);

  useConnectedSource({
    apiKey,
    type: "magento",
    matches: (s) => s.type === "magento" || isMagentoPushSource(s),
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
            // Products sent by the wizard: keeping the store means sending them again, up to date.
            if (canRun && cli && isMagentoPushSource(existing)) return setStage("creating");
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

  if (stage === "plan") {
    const total = auto.current.items.length;
    return (
      <Box flexDirection="column">
        <Text>
          Your store has {total} products; your {plan.planName} plan has room for {plan.room} more in LIKYLY.
        </Text>
        <Text color={C.muted}>LIKYLY only recommends products it has: on the page of a product left out, the block stays empty.</Text>
        <Box marginTop={1}>
          <Select
            items={[
              { value: "categories", label: `Choose the categories to send (up to ${plan.room} products)` },
              { value: "pro", label: "Get a bigger plan", hint: plan.upgradeUrl },
              { value: "anyway", label: `Send them all anyway: the first ${plan.room} are kept` },
            ]}
            onSubmit={(choice) => {
              if (choice === "anyway") return proceed(null);
              if (choice === "categories") return setStage("categories");
              if (plan.upgradeUrl) openUrl(plan.upgradeUrl);
              onDone(
                [
                  { level: "warn", text: `Not connected yet: ${total} products, room for ${plan.room} on the ${plan.planName} plan. Once your plan is bigger, run:`, hint: LATER },
                  ...(plan.upgradeUrl ? [{ level: "info", text: "Opened in your browser:", hint: plan.upgradeUrl }] : []),
                ],
                "skipped",
              );
            }}
          />
        </Box>
      </Box>
    );
  }

  if (stage === "categories") {
    const counts = categoryCounts(auto.current.items);
    return (
      <Box flexDirection="column">
        <Text>Which categories should LIKYLY recommend from? Room for {plan.room} products.</Text>
        {error ? <Text color={C.coral}>✗ {error}</Text> : null}
        <MultiSelect
          items={counts.map((c) => ({ value: c.name ?? "\u0000none", label: c.name ?? "Products without a category", hint: `${c.count} product(s)` }))}
          onSubmit={(values) => {
            const names = values.map((v) => (v === "\u0000none" ? null : v));
            const count = itemsInCategories(auto.current.items, names).length;
            if (!count) return setError("Pick at least one category (space), then Enter.");
            if (count > plan.room) return setError(`That's ${count} products: room for ${plan.room}. Untick one.`);
            setError(null);
            proceed(names);
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

  if (stage === "creating" || stage === "enabling" || stage === "integration")
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
      {stage === "pushing"
        ? `Sending the products of ${storeUrl} to LIKYLY${fetched ? `: ${fetched}/${auto.current?.items?.length ?? "?"}` : "…"}`
        : fetched
          ? `Syncing ${storeUrl}: ${fetched} product(s) read so far…`
          : `Connecting ${storeUrl} to LIKYLY and syncing its products (about a minute)…`}
    </Text>
  );
}
