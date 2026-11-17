// Magento 2 (Open Source, Mage-OS, Adobe Commerce): the module the wizard writes, its public key
// file, and the catalog connection (an Integration's credentials, typed in the terminal and sent
// straight to the LIKYLY API, like Shopify's).

import { existsSync as exists, mkdirSync as mkdir, readFileSync as read, realpathSync, writeFileSync as write } from "node:fs";
import { dirname as parentOf, join as joinPath, resolve as resolvePath } from "node:path";
import { connectCatalog, pushCatalog } from "./catalog.mjs";
import { API_URL } from "./verify.mjs";

/** "shop.example.com", "https://shop.example.com/admin/..." -> "https://shop.example.com". */
export function normalizeStoreUrl(input) {
  let v = String(input ?? "").trim();
  if (!v) return null;
  if (!/^https?:\/\//i.test(v)) v = `${/^(localhost|127\.|\[?::1)/.test(v) ? "http" : "https"}://${v}`;
  let url;
  try {
    url = new URL(v);
  } catch {
    return null;
  }
  if (!url.hostname.includes(".") && url.hostname !== "localhost") return null;
  // The admin or a REST path pasted by mistake: keep only the store's own root.
  const path = url.pathname.replace(/\/(admin|rest|index\.php)(\/.*)?$/i, "").replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}

/** The catalog namespace: the store's host ("https://www.shop.example.com" -> "shop-example-com"). */
export function magentoCatalogName(storeUrl) {
  const host = new URL(storeUrl).hostname.replace(/^www\./, "");
  if (host === "localhost" || /^[\d.]+$/.test(host)) return "magento";
  return host.replace(/[^a-zA-Z0-9_-]+/g, "-").slice(0, 64);
}

/** The four values Magento shows once an Integration is activated (all required to sign requests). */
export function magentoCredentials({ consumerKey, consumerSecret, accessToken, accessTokenSecret }) {
  const c = { consumer_key: consumerKey, consumer_secret: consumerSecret, access_token: accessToken, access_token_secret: accessTokenSecret };
  for (const k of Object.keys(c)) c[k] = String(c[k] ?? "").trim();
  return Object.values(c).every(Boolean) ? c : null;
}

/** Create (or, for the same store, update) the `magento` source, test it, start the first sync. */
export function connectMagentoCatalog({ apiKey, storeUrl, storeCode, categories, credentials, ...rest }) {
  return connectCatalog({
    apiKey,
    platform: "Magento",
    agent: "magento",
    label: storeUrl.replace(/^https?:\/\//, ""),
    matches: (s) => s.type === "magento" && s.config?.store_url === storeUrl,
    source: { name: `Magento · ${storeUrl.replace(/^https?:\/\//, "")}`, type: "magento", product_type: magentoCatalogName(storeUrl), config: { store_url: storeUrl, ...(storeCode && storeCode !== "default" ? { store_code: storeCode } : {}), ...(categories?.length ? { categories } : {}) } },
    credentials,
    ...rest,
  });
}

/**
 * A URL only this computer (or its network) reaches: localhost, a private address, or a local
 * development domain (.test, .local, .localhost, DDEV, Lando, Warden).
 */
export function isLocalUrl(url) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^\[|\]$/g, "");
  } catch {
    return false;
  }
  if (!host.includes(".") || host === "::1") return true;
  if (/^(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)) return true;
  return /\.(localhost|local|test|internal|lan|ddev\.site|lndo\.site)$/.test(host);
}

/**
 * LIKYLY reads a store itself (pull) unless the store is local and LIKYLY isn't.
 * LIKYLY_MAGENTO_PUSH=1 forces sending (a store behind a VPN or a password, whose address looks
 * public), =0 forces reading.
 */
export function needsPush(storeUrl, apiUrl = API_URL, env = process.env) {
  if (env.LIKYLY_MAGENTO_PUSH === "1") return true;
  if (env.LIKYLY_MAGENTO_PUSH === "0") return false;
  return isLocalUrl(storeUrl) && !isLocalUrl(apiUrl);
}

/** How a pushed Magento item (bin/magento likyly:catalog) maps to a LIKYLY item. */
export const MAGENTO_PUSH_MAPPING = { external_id: "id", title: "title", description: "description", category: "categories", price: "price", image: "image", url: "url", stock: "stock" };

/** A push source (webhook) the wizard filled for this Magento store. */
export const isMagentoPushSource = (s, storeUrl) => s.type === "webhook" && s.config?.platform === "magento" && (!storeUrl || s.config?.store_url === storeUrl);

/** Send a local store's products (from bin/magento likyly:catalog) to LIKYLY's push source for it. */
export function pushMagentoCatalog({ apiKey, storeUrl, items, ...rest }) {
  const label = storeUrl.replace(/^https?:\/\//, "");
  return pushCatalog({
    apiKey,
    platform: "Magento",
    agent: "magento",
    label,
    matches: (s) => isMagentoPushSource(s, storeUrl),
    source: {
      name: `Magento · ${label} (sent by the wizard)`,
      type: "webhook",
      product_type: magentoCatalogName(storeUrl),
      config: { platform: "magento", store_url: storeUrl, sample: items.slice(0, 3) },
      field_mapping: MAGENTO_PUSH_MAPPING,
    },
    items,
    // The store's products, as chosen: they replace what the catalog had (a new choice of
    // categories frees the room the previous one took).
    replace: true,
    ...rest,
  });
}

// --- The module ---------------------------------------------------------------------------------

const xml = (body, schema) => `<?xml version="1.0"?>
<!-- Generated by @likyly/wizard. -->
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="${schema}">
${body}
</config>
`;

const escapeXml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * etc/config.xml: the module's default settings, i.e. the public key (and the API address when
 * it is not production). A merchant can override both per store in Stores > Configuration >
 * Catalog > LIKYLY. Rewritten by the wizard when the key changes.
 */
export function magentoConfigXml(key, apiUrl) {
  const api = apiUrl && apiUrl !== "https://api.likyly.com" ? `\n                <api_url>${escapeXml(apiUrl)}</api_url>` : "";
  return xml(
    `    <!-- LIKYLY public key (pk_...): public, safe in the storefront. -->
    <default>
        <likyly>
            <general>
                <public_key>${escapeXml(key)}</public_key>${api}
            </general>
        </likyly>
    </default>`,
    "urn:magento:module:Magento_Store:etc/config.xsd",
  );
}

const REGISTRATION = `<?php
// LIKYLY recommendations for Magento. Generated by @likyly/wizard.
use Magento\\Framework\\Component\\ComponentRegistrar;

ComponentRegistrar::register(ComponentRegistrar::MODULE, 'Likyly_Recommendations', __DIR__);
`;

const MODULE_XML = xml(
  `    <module name="Likyly_Recommendations">
        <sequence>
            <module name="Magento_Catalog"/>
            <module name="Magento_Widget"/>
            <module name="Magento_Integration"/>
            <module name="Magento_Customer"/>
            <module name="Magento_Checkout"/>
        </sequence>
    </module>`,
  "urn:magento:framework:Module/etc/module.xsd",
);

const SYSTEM_XML = xml(
  `    <system>
        <section id="likyly" translate="label" sortOrder="900" showInDefault="1" showInWebsite="1" showInStore="1">
            <label>LIKYLY</label>
            <tab>catalog</tab>
            <resource>Magento_Catalog::config_catalog</resource>
            <group id="general" translate="label" sortOrder="10" showInDefault="1" showInWebsite="1" showInStore="1">
                <label>Recommendations</label>
                <field id="public_key" translate="label comment" type="text" sortOrder="10" showInDefault="1" showInWebsite="1" showInStore="1">
                    <label>Public key</label>
                    <comment>pk_... from your LIKYLY console (Account > API keys). Public: safe in the storefront.</comment>
                </field>
                <field id="api_url" translate="label comment" type="text" sortOrder="20" showInDefault="1" showInWebsite="1" showInStore="1">
                    <label>API address</label>
                    <comment>Leave empty for https://api.likyly.com.</comment>
                </field>
                <field id="catalog" translate="label comment" type="text" sortOrder="30" showInDefault="1" showInWebsite="1" showInStore="1">
                    <label>Catalog</label>
                    <comment>The LIKYLY catalog of this store. Leave empty: the one the wizard created for it (named after the store's address, "magento" on this computer).</comment>
                </field>
            </group>
        </section>
    </system>`,
  "urn:magento:module:Magento_Config:etc/system_file.xsd",
);

const WIDGET_XML = `<?xml version="1.0"?>
<!-- Generated by @likyly/wizard. Content > Widgets > Add Widget > "LIKYLY recommendations". -->
<widgets xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:module:Magento_Widget:etc/widget.xsd">
    <widget id="likyly_recommendations" class="Likyly\\Recommendations\\Block\\Recommendations">
        <label translate="true">LIKYLY recommendations</label>
        <description translate="true">Products recommended by LIKYLY for one placement</description>
        <parameters>
            <parameter name="placement" xsi:type="text" required="true" visible="true" sort_order="10">
                <label translate="true">LIKYLY placement</label>
                <description translate="true">The placement's slug in LIKYLY. Its strategy, fallback and filters are set there.</description>
            </parameter>
            <parameter name="heading" xsi:type="text" visible="true" sort_order="20">
                <label translate="true">Heading</label>
                <value>You may also like</value>
            </parameter>
            <parameter name="limit" xsi:type="text" visible="true" sort_order="30">
                <label translate="true">Products</label>
                <value>4</value>
            </parameter>
        </parameters>
    </widget>
</widgets>
`;

const CSP_XML = `<?xml version="1.0"?>
<!-- Generated by @likyly/wizard: the SDK comes from jsDelivr, recommendations from the LIKYLY API. -->
<csp_whitelist xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:module:Magento_Csp:etc/csp_whitelist.xsd">
    <policies>
        <policy id="script-src">
            <values>
                <value id="likyly-sdk" type="host">https://cdn.jsdelivr.net</value>
            </values>
        </policy>
        <policy id="connect-src">
            <values>
                <value id="likyly-api" type="host">https://api.likyly.com</value>
            </values>
        </policy>
    </policies>
</csp_whitelist>
`;

const BLOCK_PHP = `<?php
declare(strict_types=1);

namespace Likyly\\Recommendations\\Block;

use Magento\\Catalog\\Model\\Product;
use Magento\\Framework\\Registry;
use Magento\\Framework\\View\\Element\\Template;
use Magento\\Store\\Model\\ScopeInterface;
use Magento\\Widget\\Block\\BlockInterface;

/**
 * LIKYLY recommendations for one placement. Generated by @likyly/wizard.
 *
 * In a layout file (view/frontend/layout/catalog_product_view.xml for the product page):
 *   <referenceContainer name="content.aside">
 *     <block class="Likyly\\Recommendations\\Block\\Recommendations" name="likyly.product" before="-">
 *       <arguments><argument name="placement" xsi:type="string">pdp-related</argument></arguments>
 *     </block>
 *   </referenceContainer>
 * or as a widget: Content > Widgets > "LIKYLY recommendations".
 *
 * The HTML holds nothing about the visitor, so pages stay in the full-page cache: the visitor's
 * anonymous id is read in the browser. On a product page the product's SKU is sent as its id:
 * the id LIKYLY's Magento connector gives it.
 */
class Recommendations extends Template implements BlockInterface
{
    protected $_template = 'Likyly_Recommendations::recommendations.phtml';

    public function __construct(Template\\Context $context, private readonly Registry $registry, array $data = [])
    {
        parent::__construct($context, $data);
    }

    public function getPublicKey(): string
    {
        return trim((string) $this->_scopeConfig->getValue('likyly/general/public_key', ScopeInterface::SCOPE_STORE));
    }

    public function getApiUrl(): string
    {
        return rtrim(trim((string) $this->_scopeConfig->getValue('likyly/general/api_url', ScopeInterface::SCOPE_STORE)), '/');
    }

    /**
     * The LIKYLY catalog of this store, sent with every call: once the account has several
     * catalogs, an event that does not name its own is refused. The one set in the admin, else the
     * one the wizard created for this store (same rule as its magentoCatalogName).
     */
    public function getCatalog(): string
    {
        $configured = trim((string) $this->_scopeConfig->getValue('likyly/general/catalog', ScopeInterface::SCOPE_STORE));
        if ($configured !== '') {
            return $configured;
        }
        $host = preg_replace('/^www\\./', '', (string) parse_url((string) $this->_storeManager->getStore()->getBaseUrl(), PHP_URL_HOST));
        if ($host === '' || $host === 'localhost' || preg_match('/^[\\d.]+$/', $host)) {
            return 'magento';
        }
        return substr(preg_replace('/[^a-zA-Z0-9_-]+/', '-', $host), 0, 64);
    }

    public function getPlacement(): string
    {
        return trim((string) $this->getData('placement'));
    }

    public function getHeading(): string
    {
        $heading = $this->getData('heading');
        return $heading === null ? (string) __('You may also like') : (string) $heading;
    }

    public function getLimit(): int
    {
        $limit = (int) $this->getData('limit');
        return $limit > 0 ? min($limit, 24) : 4;
    }

    /** The product shown on a product page (its SKU), empty elsewhere. */
    public function getItemId(): string
    {
        $product = $this->registry->registry('current_product');
        return $product instanceof Product ? (string) $product->getSku() : '';
    }

    public function getCurrencyCode(): string
    {
        return (string) $this->_storeManager->getStore()->getCurrentCurrencyCode();
    }

    public function getLocale(): string
    {
        return str_replace('_', '-', (string) $this->_scopeConfig->getValue('general/locale/code', ScopeInterface::SCOPE_STORE));
    }
}
`;

const TEMPLATE_PHTML = `<?php
/**
 * LIKYLY recommendations. Generated by @likyly/wizard. The cards come from LIKYLY (title, image,
 * price and link of each product, read from this store's catalog); the SDK sends the product view,
 * the impressions (when the block is seen) and the clicks. A signed-in customer gets
 * recommendations from their own history (tracking.phtml tells who they are); a click is
 * remembered so the add to cart and the purchase that follow count for the recommendation.
 *
 * @var \\Likyly\\Recommendations\\Block\\Recommendations $block
 * @var \\Magento\\Framework\\Escaper $escaper
 * @var \\Magento\\Framework\\View\\Helper\\SecureHtmlRenderer $secureRenderer
 */
$placement = $block->getPlacement();
if ($placement === '') {
    return;
}
$id = 'likyly-' . substr(sha1($placement . $block->getNameInLayout() . $block->getItemId()), 0, 10);
$heading = $block->getHeading();
?>
<div class="block likyly-recs" id="<?= $escaper->escapeHtmlAttr($id) ?>"
     data-key="<?= $escaper->escapeHtmlAttr($block->getPublicKey()) ?>"
     data-api-url="<?= $escaper->escapeHtmlAttr($block->getApiUrl()) ?>"
     data-catalog="<?= $escaper->escapeHtmlAttr($block->getCatalog()) ?>"
     data-placement="<?= $escaper->escapeHtmlAttr($placement) ?>"
     data-limit="<?= (int) $block->getLimit() ?>"
     data-item-id="<?= $escaper->escapeHtmlAttr($block->getItemId()) ?>"
     data-currency="<?= $escaper->escapeHtmlAttr($block->getCurrencyCode()) ?>"
     data-locale="<?= $escaper->escapeHtmlAttr($block->getLocale()) ?>"
     hidden>
    <?php if ($heading !== ''): ?>
        <div class="block-title title"><strong role="heading" aria-level="2"><?= $escaper->escapeHtml($heading) ?></strong></div>
    <?php endif; ?>
    <ul class="likyly-recs__grid" data-likyly-placement="<?= $escaper->escapeHtmlAttr($placement) ?>"></ul>
</div>
<?= /* @noEscape */ $secureRenderer->renderTag('style', [], '
.likyly-recs { margin: 2rem 0; }
.likyly-recs__grid { list-style: none; margin: 0; padding: 0; display: grid; gap: 1.25rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 12rem), 1fr)); }
.likyly-recs__grid li { margin: 0; }
.likyly-recs__card { display: flex; flex-direction: column; gap: .4rem; color: inherit; text-decoration: none; }
.likyly-recs__card:hover { text-decoration: none; }
.likyly-recs__card img { width: 100%; aspect-ratio: 1; object-fit: cover; background: #f5f5f5; }
.likyly-recs__price { font-weight: 600; }
', false) ?>
<?php $script = <<<'JS'
import { Likyly, LikylySession } from "https://cdn.jsdelivr.net/npm/@likyly/sdk@1/+esm";

const root = document.getElementById("__ID__");
const { key, apiUrl, catalog, placement, itemId, currency, locale } = root.dataset;
if (!key.startsWith("pk_")) {
  console.warn("LIKYLY: no public key (Stores > Configuration > Catalog > LIKYLY)");
} else {
  const likyly = new Likyly({ apiKey: key, ...(apiUrl ? { baseUrl: apiUrl } : {}), ...(catalog ? { catalog } : {}) });
  const sessionId = new LikylySession().getAnonymousId();
  // The signed-in customer (tracking.phtml, in the page head), none for a guest.
  const tracker = window.likyly || {};
  const { userId } = await (tracker.visitor || Promise.resolve({}));
  const who = userId ? { userId, sessionId, anonymousId: sessionId } : { sessionId, anonymousId: sessionId };
  const price = (value) => {
    if (value === undefined || value === null || value === "" || !currency) return "";
    try {
      return new Intl.NumberFormat(locale || undefined, { style: "currency", currency }).format(Number(value));
    } catch {
      return String(value);
    }
  };
  if (itemId) likyly.events.view({ ...who, itemId, placement: "product_page" }).catch(() => {});
  try {
    const recs = await likyly.recommend({ placement, context: { itemId: itemId || undefined, userId, sessionId, anonymousId: sessionId }, limit: Number(root.dataset.limit) || 4 });
    const items = recs.items.filter((item) => item.itemId !== itemId);
    const grid = root.querySelector(".likyly-recs__grid");
    for (const item of items) {
      const p = item.properties || {};
      const link = document.createElement("a");
      link.className = "likyly-recs__card";
      link.href = p.url || "#";
      if (p.image) {
        const img = document.createElement("img");
        img.src = p.image;
        img.alt = item.title || "";
        img.loading = "lazy";
        link.append(img);
      }
      const title = document.createElement("span");
      title.textContent = item.title || item.itemId;
      link.append(title);
      const amount = price(p.price);
      if (amount) {
        const tag = document.createElement("span");
        tag.className = "likyly-recs__price";
        tag.textContent = amount;
        link.append(tag);
      }
      link.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || !p.url) return;
        event.preventDefault();
        if (tracker.remember) tracker.remember(item.itemId, recs.recommendationId, placement);
        const click = likyly.events.click({ ...who, itemId: item.itemId, recommendationId: recs.recommendationId, placement });
        Promise.race([click, new Promise((r) => setTimeout(r, 400))]).finally(() => (location.href = p.url));
      });
      const li = document.createElement("li");
      li.dataset.likylyItemId = item.itemId;
      li.append(link);
      grid.append(li);
    }
    if (items.length) {
      root.hidden = false;
      new IntersectionObserver((entries, observer) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        observer.disconnect();
        for (const item of items) likyly.events.impression({ ...who, itemId: item.itemId, recommendationId: recs.recommendationId, placement }).catch(() => {});
      }).observe(root);
    }
  } catch (error) {
    console.warn("LIKYLY: no recommendations for placement " + placement, error);
  }
}
JS;
?>
<?= /* @noEscape */ $secureRenderer->renderTag('script', ['type' => 'module'], str_replace('__ID__', $id, $script), false) ?>
`;

// `bin/magento likyly:integration`: what a merchant would click in the admin (System > Integrations >
// Add, resource access, Save, Activate), done by the wizard. Read-only access to the catalog, the
// stores and the stock; the credentials are printed as one JSON line for the wizard, which sends
// them straight to LIKYLY (never shown, never written to a file).
const DI_XML = xml(
  `    <type name="Magento\\Framework\\Console\\CommandListInterface">
        <arguments>
            <argument name="commands" xsi:type="array">
                <item name="likyly_integration" xsi:type="object">Likyly\\Recommendations\\Console\\Command\\IntegrationCommand</item>
                <item name="likyly_catalog" xsi:type="object">Likyly\\Recommendations\\Console\\Command\\CatalogCommand</item>
            </argument>
        </arguments>
    </type>
    <!-- Proxies: every bin/magento call builds the command list, this one stays cheap. -->
    <type name="Likyly\\Recommendations\\Console\\Command\\IntegrationCommand">
        <arguments>
            <argument name="state" xsi:type="object">Magento\\Framework\\App\\State\\Proxy</argument>
            <argument name="integrations" xsi:type="object">Magento\\Integration\\Api\\IntegrationServiceInterface\\Proxy</argument>
            <argument name="oauth" xsi:type="object">Magento\\Integration\\Api\\OauthServiceInterface\\Proxy</argument>
            <argument name="authorization" xsi:type="object">Magento\\Integration\\Api\\AuthorizationServiceInterface\\Proxy</argument>
            <argument name="storeManager" xsi:type="object">Magento\\Store\\Model\\StoreManagerInterface\\Proxy</argument>
        </arguments>
    </type>
    <type name="Likyly\\Recommendations\\Console\\Command\\CatalogCommand">
        <arguments>
            <argument name="state" xsi:type="object">Magento\\Framework\\App\\State\\Proxy</argument>
            <argument name="products" xsi:type="object">Magento\\Catalog\\Model\\ResourceModel\\Product\\CollectionFactory\\Proxy</argument>
            <argument name="categories" xsi:type="object">Magento\\Catalog\\Model\\ResourceModel\\Category\\CollectionFactory\\Proxy</argument>
            <argument name="stockRegistry" xsi:type="object">Magento\\CatalogInventory\\Api\\StockRegistryInterface\\Proxy</argument>
            <argument name="storeManager" xsi:type="object">Magento\\Store\\Model\\StoreManagerInterface\\Proxy</argument>
            <argument name="scopeConfig" xsi:type="object">Magento\\Framework\\App\\Config\\ScopeConfigInterface\\Proxy</argument>
        </arguments>
    </type>`,
  "urn:magento:framework:ObjectManager/etc/config.xsd",
);

const COMMAND_PHP = `<?php
declare(strict_types=1);

namespace Likyly\\Recommendations\\Console\\Command;

use Magento\\Framework\\App\\Area;
use Magento\\Framework\\App\\State;
use Magento\\Framework\\Exception\\LocalizedException;
use Magento\\Framework\\UrlInterface;
use Magento\\Integration\\Api\\AuthorizationServiceInterface;
use Magento\\Integration\\Api\\IntegrationServiceInterface;
use Magento\\Integration\\Api\\OauthServiceInterface;
use Magento\\Integration\\Model\\Integration;
use Magento\\Store\\Model\\StoreManagerInterface;
use Symfony\\Component\\Console\\Command\\Command;
use Symfony\\Component\\Console\\Input\\InputInterface;
use Symfony\\Component\\Console\\Input\\InputOption;
use Symfony\\Component\\Console\\Output\\OutputInterface;

/**
 * Generated by @likyly/wizard. Creates (or reuses) the Integration LIKYLY reads the catalog with -
 * read-only: products, categories, stores, stock - activates it, and prints one JSON line:
 * {store_url, store_code, consumer_key, consumer_secret, access_token, access_token_secret}.
 * Run by npx @likyly/wizard, which sends it straight to LIKYLY. It is the same Integration a
 * merchant would make in System > Extensions > Integrations, and can be revoked there.
 */
class IntegrationCommand extends Command
{
    private const RESOURCES = [
        'Magento_Catalog::catalog',
        'Magento_Catalog::catalog_inventory',
        'Magento_Catalog::products',
        'Magento_Catalog::categories',
        'Magento_Backend::stores',
        'Magento_Backend::stores_settings',
        'Magento_Backend::store',
        'Magento_InventoryApi::inventory',
        'Magento_InventoryApi::source',
        'Magento_CatalogInventory::cataloginventory',
    ];

    public function __construct(
        private readonly State $state,
        private readonly IntegrationServiceInterface $integrations,
        private readonly OauthServiceInterface $oauth,
        private readonly AuthorizationServiceInterface $authorization,
        private readonly StoreManagerInterface $storeManager
    ) {
        parent::__construct();
    }

    protected function configure(): void
    {
        $this->setName('likyly:integration')
            ->setDescription('Create (or reuse) the read-only LIKYLY Integration and print its credentials as JSON (for npx @likyly/wizard)')
            ->addOption('name', null, InputOption::VALUE_REQUIRED, 'Integration name', 'LIKYLY');
        parent::configure();
    }

    protected function execute(InputInterface $input, OutputInterface $output): int
    {
        try {
            $this->state->setAreaCode(Area::AREA_ADMINHTML);
        } catch (LocalizedException $e) {
            // area already set
        }
        $name = (string) $input->getOption('name');
        $integration = $this->integrations->findByName($name);
        if (!$integration->getId()) {
            $integration = $this->integrations->create([
                'name' => $name,
                'email' => '',
                'endpoint' => '',
                'identity_link_url' => '',
                'setup_type' => Integration::TYPE_MANUAL,
                'status' => Integration::STATUS_INACTIVE,
                'resource' => self::RESOURCES,
            ]);
        }
        $this->authorization->grantPermissions((int) $integration->getId(), self::RESOURCES);
        $consumerId = (int) $integration->getConsumerId();
        if (!$this->oauth->getAccessToken($consumerId)) {
            $this->oauth->createAccessToken($consumerId, true);
        }
        $integration->setStatus(Integration::STATUS_ACTIVE)->save();

        $consumer = $this->oauth->loadConsumer($consumerId);
        $token = $this->oauth->getAccessToken($consumerId);
        $store = $this->storeManager->getDefaultStoreView();
        $url = $store ? $store->getBaseUrl(UrlInterface::URL_TYPE_WEB, $store->isFrontUrlSecure()) : '';
        $output->writeln((string) json_encode([
            'store_url' => rtrim($url, '/'),
            'store_code' => $store ? $store->getCode() : 'default',
            'consumer_key' => $consumer->getKey(),
            'consumer_secret' => $consumer->getSecret(),
            'access_token' => $token->getToken(),
            'access_token_secret' => $token->getSecret(),
        ], JSON_UNESCAPED_SLASHES));
        return Command::SUCCESS;
    }
}
`;

// `bin/magento likyly:catalog`: the store's products as LIKYLY items, one JSON line each, for a
// store LIKYLY's servers can't reach (on the developer's computer): the wizard sends them itself.
// `--store` prints only the store's address. No Integration needed for that.
const CATALOG_PHP = String.raw`<?php
declare(strict_types=1);

namespace Likyly\Recommendations\Console\Command;

use Magento\Catalog\Model\Product\Visibility;
use Magento\Catalog\Model\ResourceModel\Category\CollectionFactory as CategoryCollectionFactory;
use Magento\Catalog\Model\ResourceModel\Product\CollectionFactory as ProductCollectionFactory;
use Magento\CatalogInventory\Api\StockRegistryInterface;
use Magento\Framework\App\Area;
use Magento\Framework\App\Config\ScopeConfigInterface;
use Magento\Framework\App\State;
use Magento\Framework\Exception\LocalizedException;
use Magento\Framework\UrlInterface;
use Magento\Store\Model\ScopeInterface;
use Magento\Store\Model\StoreManagerInterface;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;

/**
 * Generated by @likyly/wizard. Prints the default store's address as a first JSON line
 * ({"store_url", "store_code", "count"}), then one JSON line per enabled, visible product:
 * {"id" (SKU), "title", "description", "categories", "price", "image", "url", "stock"}.
 * Read-only. Run by npx @likyly/wizard when the store runs on the developer's computer.
 */
class CatalogCommand extends Command
{
    public function __construct(
        private readonly State $state,
        private readonly ProductCollectionFactory $products,
        private readonly CategoryCollectionFactory $categories,
        private readonly StockRegistryInterface $stockRegistry,
        private readonly StoreManagerInterface $storeManager,
        private readonly ScopeConfigInterface $scopeConfig
    ) {
        parent::__construct();
    }

    protected function configure(): void
    {
        $this->setName('likyly:catalog')
            ->setDescription('Print the store and its products as JSON lines, for npx @likyly/wizard (read-only)')
            ->addOption('store', null, InputOption::VALUE_NONE, 'Print only the store line');
        parent::configure();
    }

    protected function execute(InputInterface $input, OutputInterface $output): int
    {
        try {
            $this->state->setAreaCode(Area::AREA_FRONTEND);
        } catch (LocalizedException $e) {
            // area already set
        }
        $store = $this->storeManager->getDefaultStoreView();
        if (!$store) {
            $output->writeln('No default store view');
            return Command::FAILURE;
        }
        $storeId = (int) $store->getId();
        $secure = $store->isFrontUrlSecure();
        $base = $store->getBaseUrl(UrlInterface::URL_TYPE_WEB, $secure);
        $collection = $this->products->create()
            ->setStoreId($storeId)
            ->addStoreFilter($storeId)
            ->addAttributeToSelect(['name', 'description', 'short_description', 'price', 'image', 'small_image', 'url_key'])
            ->addAttributeToFilter('status', 1)
            ->addAttributeToFilter('visibility', ['in' => [Visibility::VISIBILITY_IN_CATALOG, Visibility::VISIBILITY_IN_SEARCH, Visibility::VISIBILITY_BOTH]]);
        $output->writeln((string) json_encode(['store_url' => rtrim($base, '/'), 'store_code' => $store->getCode(), 'count' => $collection->getSize()], JSON_UNESCAPED_SLASHES));
        if ($input->getOption('store')) {
            return Command::SUCCESS;
        }

        $media = $store->getBaseUrl(UrlInterface::URL_TYPE_MEDIA, $secure) . 'catalog/product';
        $suffix = (string) $this->scopeConfig->getValue('catalog/seo/product_url_suffix', ScopeInterface::SCOPE_STORE, $storeId);
        $names = [];
        foreach ($this->categories->create()->setStoreId($storeId)->addAttributeToSelect('name')->addAttributeToFilter('level', ['gteq' => 2]) as $category) {
            $names[(int) $category->getId()] = (string) $category->getName();
        }

        $collection->setPageSize(500);
        $pages = $collection->getLastPageNumber();
        for ($page = 1; $page <= $pages; $page++) {
            $collection->setCurPage($page)->clear();
            foreach ($collection as $product) {
                $image = (string) ($product->getData('image') ?: $product->getData('small_image'));
                $stockItem = $this->stockRegistry->getStockItem((int) $product->getId());
                $stock = null;
                if ($stockItem->getManageStock()) {
                    $qty = (int) $stockItem->getQty();
                    $stock = !$stockItem->getIsInStock() ? 0 : ($qty > 0 ? $qty : null);
                }
                $categories = [];
                foreach ($product->getCategoryIds() as $id) {
                    if (isset($names[(int) $id])) {
                        $categories[] = $names[(int) $id];
                    }
                }
                $urlKey = (string) $product->getData('url_key');
                $output->writeln((string) json_encode([
                    'id' => (string) $product->getSku(),
                    'title' => (string) $product->getName(),
                    'description' => (string) ($product->getData('description') ?: $product->getData('short_description')),
                    'categories' => $categories,
                    'price' => $product->getData('price') === null ? null : (float) $product->getData('price'),
                    'image' => $image && $image !== 'no_selection' ? $media . '/' . ltrim($image, '/') : null,
                    'url' => $urlKey !== '' ? $base . $urlKey . $suffix : null,
                    'stock' => $stock,
                ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
            }
        }
        return Command::SUCCESS;
    }
}
`;

/** The module's files, relative to app/code/Likyly/Recommendations (etc/config.xml is the key file). */

const TRACKING_PHP = String.raw`<?php
declare(strict_types=1);

namespace Likyly\Recommendations\Block;

use Magento\Checkout\Model\Session as CheckoutSession;
use Magento\Framework\Registry;
use Magento\Framework\View\Element\Template;

/**
 * LIKYLY tracking, in the head of every page (layout default.xml). Generated by @likyly/wizard.
 *
 * Tells LIKYLY who the visitor is once signed in (read in the browser from Magento's customer
 * data, so pages stay in the full-page cache), sends each add to cart, and on the order
 * confirmation page the purchase: every product with its price and quantity, and the
 * recommendation that brought it - what LIKYLY counts as revenue.
 */
class Tracking extends Recommendations
{
    protected $_template = 'Likyly_Recommendations::tracking.phtml';

    public function __construct(
        Template\Context $context,
        Registry $registry,
        private readonly CheckoutSession $checkoutSession,
        array $data = []
    ) {
        parent::__construct($context, $registry, $data);
    }

    /** The order just placed, on the confirmation page only (null elsewhere). */
    public function getOrder(): ?array
    {
        if ($this->getRequest()->getFullActionName() !== 'checkout_onepage_success') {
            return null;
        }
        $order = $this->checkoutSession->getLastRealOrder();
        if (!$order || !$order->getIncrementId()) {
            return null;
        }
        $lines = [];
        foreach ($order->getAllVisibleItems() as $item) {
            $quantity = (float) $item->getQtyOrdered() ?: 1.0;
            // The product's own SKU (a configurable product's, not its variant's): LIKYLY's id.
            $product = $item->getProduct();
            $sku = $product && $product->getSku() ? (string) $product->getSku() : (string) $item->getSku();
            // What the customer paid for one, discounts included, taxes not.
            $paid = (float) $item->getRowTotal() - (float) $item->getDiscountAmount();
            $lines[] = ['sku' => $sku, 'quantity' => $quantity, 'price' => round($paid / $quantity, 2)];
        }
        return [
            'id' => (string) $order->getIncrementId(),
            'currency' => (string) $order->getOrderCurrencyCode(),
            'userId' => $order->getCustomerId() ? (string) $order->getCustomerId() : null,
            'lines' => $lines,
        ];
    }
}
`;

const VISITOR_PHP = String.raw`<?php
declare(strict_types=1);

namespace Likyly\Recommendations\CustomerData;

use Magento\Customer\CustomerData\SectionSourceInterface;
use Magento\Customer\Model\Session;

/**
 * The "likyly" customer data section: the signed-in customer's id, null for a guest. Generated by
 * @likyly/wizard. Magento loads it in the browser (customer-data.js), never in cached HTML.
 */
class Visitor implements SectionSourceInterface
{
    public function __construct(private readonly Session $session)
    {
    }

    public function getSectionData(): array
    {
        $id = $this->session->getCustomerId();
        return ['userId' => $id ? (string) $id : null];
    }
}
`;

const FRONTEND_DI_XML = xml(
  String.raw`    <type name="Magento\Customer\CustomerData\SectionPoolInterface">
        <arguments>
            <argument name="sectionSourceMap" xsi:type="array">
                <item name="likyly" xsi:type="string">Likyly\Recommendations\CustomerData\Visitor</item>
            </argument>
        </arguments>
    </type>
    <type name="Likyly\Recommendations\CustomerData\Visitor">
        <arguments>
            <argument name="session" xsi:type="object">Magento\Customer\Model\Session\Proxy</argument>
        </arguments>
    </type>
    <type name="Likyly\Recommendations\Block\Tracking">
        <arguments>
            <argument name="checkoutSession" xsi:type="object">Magento\Checkout\Model\Session\Proxy</argument>
        </arguments>
    </type>`,
  "urn:magento:framework:ObjectManager/etc/config.xsd",
);

// Signing in, creating an account or signing out refreshes the "likyly" section in the browser.
const SECTIONS_XML = xml(
  `    <action name="customer/account/loginPost">
        <section name="likyly"/>
    </action>
    <action name="customer/account/createPost">
        <section name="likyly"/>
    </action>
    <action name="customer/ajax/login">
        <section name="likyly"/>
    </action>
    <action name="customer/account/logout">
        <section name="likyly"/>
    </action>`,
  "urn:magento:module:Magento_Customer:etc/sections.xsd",
);

const DEFAULT_LAYOUT_XML = `<?xml version="1.0"?>
<!-- Generated by @likyly/wizard: LIKYLY tracking in the head of every page (who is signed in, add to cart, purchase). -->
<page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:framework:View/Layout/etc/page_configuration.xsd">
    <body>
        <referenceBlock name="head.additional">
            <block class="Likyly\\Recommendations\\Block\\Tracking" name="likyly.tracking"/>
        </referenceBlock>
    </body>
</page>
`;

const TRACKING_PHTML = String.raw`<?php
/**
 * LIKYLY tracking. Generated by @likyly/wizard. Runs before the recommendation blocks and gives
 * them window.likyly: who the visitor is (visitor), and the recommendation each product was
 * clicked from (remember / attribution), kept 30 days in this browser.
 *
 * @var \Likyly\Recommendations\Block\Tracking $block
 * @var \Magento\Framework\View\Helper\SecureHtmlRenderer $secureRenderer
 */
$config = [
    'key' => $block->getPublicKey(),
    'apiUrl' => $block->getApiUrl(),
    'catalog' => $block->getCatalog(),
    'order' => $block->getOrder(),
];
$json = json_encode($config, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_UNESCAPED_SLASHES);
?>
<?php $script = <<<'JS'
import { Likyly, LikylySession } from "https://cdn.jsdelivr.net/npm/@likyly/sdk@1/+esm";

const config = __CONFIG__;
const DAYS_30 = 30 * 24 * 3600 * 1000;
const local = {
  get(name, fallback) {
    try {
      const raw = localStorage.getItem(name);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(name, value) {
    try {
      localStorage.setItem(name, JSON.stringify(value));
    } catch {}
  },
};
const clicks = () => {
  const all = local.get("likyly:attribution", {});
  for (const id of Object.keys(all)) if (!all[id] || Date.now() - all[id].at > DAYS_30) delete all[id];
  return all;
};

// Who is signed in: Magento's customer data, loaded in the browser ({} for a guest, or when
// Magento's scripts are not there - a few seconds at most, never blocking the page).
const visitor = new Promise((resolve) => {
  const done = (data) => resolve(data && data.userId ? { userId: String(data.userId) } : {});
  if (typeof window.require !== "function") return done(null);
  const timer = setTimeout(() => done(null), 3000);
  window.require(["Magento_Customer/js/customer-data"], (customerData) => {
    const section = customerData.get("likyly");
    const ready = (value) => value && Object.prototype.hasOwnProperty.call(value, "userId");
    if (ready(section())) {
      clearTimeout(timer);
      return done(section());
    }
    const watch = section.subscribe((value) => {
      if (!ready(value)) return;
      clearTimeout(timer);
      watch.dispose();
      done(value);
    });
    customerData.reload(["likyly"], false);
  }, () => done(null));
});

window.likyly = Object.assign(window.likyly || {}, {
  visitor,
  remember(itemId, recommendationId, placement) {
    const all = clicks();
    all[itemId] = { recommendationId, placement, at: Date.now() };
    local.set("likyly:attribution", all);
  },
  attribution(itemId) {
    return clicks()[itemId] || {};
  },
});

if (config.key && config.key.indexOf("pk_") === 0) {
  const likyly = new Likyly({ apiKey: config.key, ...(config.apiUrl ? { baseUrl: config.apiUrl } : {}), ...(config.catalog ? { catalog: config.catalog } : {}) });
  const sessionId = new LikylySession().getAnonymousId();
  visitor.then(({ userId }) => {
    const who = userId ? { userId, sessionId } : { sessionId };

    // Signed in: what this browser did before now belongs to the customer (once per session).
    if (userId && local.get("likyly:identified", "") !== userId + "/" + sessionId) {
      likyly.events
        .identify({ userId, sessionId })
        .then(() => local.set("likyly:identified", userId + "/" + sessionId))
        .catch(() => {});
    }

    // Add to cart: Magento's own AJAX add to cart (product and category pages).
    if (typeof window.require === "function") {
      window.require(["jquery"], ($) => {
        $(document).on("ajax:addToCart", (event, data) => {
          if (!data || !data.sku) return;
          const form = data.form && data.form[0];
          const qty = form ? Number((form.querySelector("[name=qty]") || {}).value) : 0;
          const from = window.likyly.attribution(String(data.sku));
          likyly.events
            .addToCart({ ...who, itemId: String(data.sku), quantity: qty > 0 ? qty : 1, recommendationId: from.recommendationId, placement: from.placement })
            .catch(() => {});
        });
      });
    }

    // The order confirmation page: one purchase per product, with its price (sent once).
    const order = config.order;
    if (order && order.lines.length && !local.get("likyly:order:" + order.id, false)) {
      const buyer = order.userId ? { userId: order.userId, sessionId } : who;
      const all = clicks();
      const events = order.lines.map((line) => {
        const from = all[line.sku] || {};
        delete all[line.sku];
        return {
          type: "purchase",
          ...buyer,
          itemId: line.sku,
          quantity: line.quantity,
          recommendationId: from.recommendationId,
          placement: from.placement,
          eventId: "purchase_" + order.id + "_" + line.sku,
          properties: { price: line.price, currency: order.currency, orderId: order.id },
        };
      });
      likyly.events
        .trackMany(events)
        .then(() => {
          local.set("likyly:attribution", all);
          local.set("likyly:order:" + order.id, true);
        })
        .catch(() => {});
    }
  });
}
JS;
?>
<?= /* @noEscape */ $secureRenderer->renderTag('script', ['type' => 'module'], str_replace('__CONFIG__', $json, $script), false) ?>
`;

export const MAGENTO_MODULE_FILES = {
  "registration.php": REGISTRATION,
  "etc/module.xml": MODULE_XML,
  "etc/adminhtml/system.xml": SYSTEM_XML,
  "etc/widget.xml": WIDGET_XML,
  "etc/csp_whitelist.xml": CSP_XML,
  "etc/di.xml": DI_XML,
  "Console/Command/IntegrationCommand.php": COMMAND_PHP,
  "Console/Command/CatalogCommand.php": CATALOG_PHP,
  "Block/Recommendations.php": BLOCK_PHP,
  "view/frontend/templates/recommendations.phtml": TEMPLATE_PHTML,
  "Block/Tracking.php": TRACKING_PHP,
  "CustomerData/Visitor.php": VISITOR_PHP,
  "etc/frontend/di.xml": FRONTEND_DI_XML,
  "etc/frontend/sections.xml": SECTIONS_XML,
  "view/frontend/layout/default.xml": DEFAULT_LAYOUT_XML,
  "view/frontend/templates/tracking.phtml": TRACKING_PHTML,
};

// --- bin/magento, run by the wizard ------------------------------------------------------------


const COMPOSE_FILES = ["compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"];

/** The Docker Compose service whose volumes mount this folder: { file, service, path } or null. */
export function composeServiceFor(dir) {
  let real;
  try {
    real = realpathSync(dir);
  } catch {
    return null;
  }
  for (const base of [real, parentOf(real)]) {
    for (const name of COMPOSE_FILES) {
      const file = joinPath(base, name);
      if (!exists(file)) continue;
      let service = null;
      let inServices = false;
      for (const line of read(file, "utf8").split("\n")) {
        if (/^services:\s*$/.test(line)) {
          inServices = true;
          continue;
        }
        if (/^\S/.test(line)) inServices = false;
        if (!inServices) continue;
        const svc = line.match(/^ {2}([A-Za-z0-9_.-]+):\s*$/);
        if (svc) {
          service = svc[1];
          continue;
        }
        const vol = line.match(/^\s+-\s+["']?([.~/][^:"'\s]*):(\/[^:"'\s]*)/);
        if (service && vol && resolvePath(base, vol[1]) === real) return { file, service, path: vol[2] };
      }
    }
  }
  return null;
}

/**
 * How bin/magento runs for this store, as { label, cmd: [...] } (cmd + a bin/magento command line):
 * DDEV, Warden, the Docker Compose service mounting this folder, or PHP on this machine. null when
 * none is found (the developer types it, or gives the Integration's values).
 */
export function detectMagentoCli(dir, { commandExists = () => true } = {}) {
  if (exists(joinPath(dir, ".ddev/config.yaml"))) return { label: "ddev magento", cmd: ["ddev", "magento"] };
  if (/^WARDEN_ENV_TYPE=/m.test(exists(joinPath(dir, ".env")) ? read(joinPath(dir, ".env"), "utf8") : "")) {
    return { label: "warden env exec php-fpm bin/magento", cmd: ["warden", "env", "exec", "-T", "php-fpm", "bin/magento"] };
  }
  const compose = composeServiceFor(dir);
  if (compose) {
    return {
      label: `docker compose exec -u www-data ${compose.service} bin/magento`,
      cmd: ["docker", "compose", "-f", compose.file, "exec", "-T", "-u", "www-data", "-w", compose.path, compose.service, "php", "bin/magento"],
    };
  }
  if (commandExists("php")) return { label: "php bin/magento", cmd: ["php", "bin/magento"] };
  return null;
}

/** "docker compose exec magento 'bin/magento'" -> words (quotes kept together). */
export function splitCommand(line) {
  return [...String(line).matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3]);
}

const quote = (w) => (/^[\w@%+=:,./-]+$/.test(w) ? w : `'${w.replace(/'/g, `'\\''`)}'`);

/**
 * Remember how bin/magento runs here (.likyly/bin-magento, a shell command line): the assistant
 * reads it to flush the cache after adding a layout file. .likyly/ ignores itself in git.
 */
export function rememberMagentoCli(dir, cmd) {
  const folder = joinPath(dir, ".likyly");
  mkdir(folder, { recursive: true });
  if (!exists(joinPath(folder, ".gitignore"))) write(joinPath(folder, ".gitignore"), "# Written by @likyly/wizard: nothing in this folder is ever committed.\n*\n");
  write(joinPath(folder, "bin-magento"), `${cmd.map(quote).join(" ")}\n`);
}

/**
 * Enable the LIKYLY module and create its read-only Integration with bin/magento - what a merchant
 * would do in the admin. Refuses a store in production mode (a module there needs a full deploy).
 * `integration: false` stops once the module is enabled (a store already connected to LIKYLY).
 * A store on this computer while LIKYLY runs in the cloud: no Integration (LIKYLY could not use
 * it); the products are read with `likyly:catalog` instead - { ok, mode: "push", storeUrl, items }.
 * `run(cmd, onLine)` resolves to true on success (setup.runAsync). Resolves to
 * { ok: true, storeUrl, storeCode, credentials } or { ok: false, error }. The credentials are
 * never passed to onLine.
 */
function magentoRunner({ cli, run, onStep, onLine }) {
  const lines = [];
  const step = async (label, args, { quiet = false } = {}) => {
    onStep(label);
    lines.length = 0;
    return run([...cli, ...args], (l) => {
      lines.push(l);
      if (!quiet && !l.trim().startsWith("{")) onLine(l);
    });
  };
  const tail = () => lines.filter((l) => !l.trim().startsWith("{")).slice(-3).join(" · ").slice(0, 300);
  return { lines, step, tail };
}

/**
 * Get the store ready, with bin/magento: refuse production mode, enable the LIKYLY module, flush
 * the cache, then (unless `catalog: false`) read the products with `likyly:catalog`. Resolves to
 * { ok: true, storeUrl, storeCode, items, mode: "push" | "pull" } - "push" when LIKYLY can't reach
 * the store (see needsPush) - or { ok: false, error }.
 */
export async function prepareMagentoStore({ dir, cli, run, onStep = () => {}, onLine = () => {}, catalog = true, apiUrl = API_URL }) {
  const { lines, step, tail } = magentoRunner({ cli, run, onStep, onLine });
  if (!(await step("Checking the store's mode", ["deploy:mode:show"]))) {
    return { ok: false, error: `bin/magento did not run (${tail() || "no output"})` };
  }
  if (/production/i.test(lines.join("\n"))) {
    return { ok: false, error: "This store runs in production mode: enabling a module there needs a full deploy (setup:upgrade, setup:di:compile, static content). Run the wizard on a development copy, or give the Integration's values yourself." };
  }
  await step("Checking the LIKYLY module", ["module:status", "Likyly_Recommendations"]);
  if (!/enabled/i.test(lines.join("\n")) || /disabled/i.test(lines.join("\n"))) {
    if (!(await step("Enabling the LIKYLY module", ["module:enable", "Likyly_Recommendations"]))) return { ok: false, error: `module:enable failed: ${tail()}` };
    if (!(await step("Updating the store (setup:upgrade, about a minute)", ["setup:upgrade", "--keep-generated"]))) return { ok: false, error: `setup:upgrade failed: ${tail()}` };
  }
  if (!(await step("Flushing the cache", ["cache:flush"]))) return { ok: false, error: `cache:flush failed: ${tail()}` };
  rememberMagentoCli(dir, cli);
  if (!catalog) return { ok: true };
  if (!(await step("Reading the store's products", ["likyly:catalog"], { quiet: true }))) return { ok: false, error: `likyly:catalog failed: ${tail()}` };
  const [store, ...items] = jsonLines(lines);
  const storeUrl = normalizeStoreUrl(store?.store_url);
  if (!storeUrl) return { ok: false, error: "likyly:catalog gave no store address" };
  return { ok: true, storeUrl, storeCode: store.store_code, items, mode: needsPush(storeUrl, apiUrl) ? "push" : "pull" };
}

/** `bin/magento likyly:integration`: the read-only Integration's credentials (never on screen). */
export async function createIntegration({ cli, run, onStep = () => {} }) {
  const { lines, step, tail } = magentoRunner({ cli, run, onStep, onLine: () => {} });
  if (!(await step("Creating the read-only LIKYLY Integration", ["likyly:integration"], { quiet: true }))) {
    return { ok: false, error: `likyly:integration failed: ${tail()}` };
  }
  const data = jsonLines(lines).at(-1);
  if (!data) return { ok: false, error: "likyly:integration gave no credentials" };
  const { store_url: url, store_code: storeCode, ...credentials } = data;
  const storeUrl = normalizeStoreUrl(url);
  if (!storeUrl || !magentoCredentials({ consumerKey: credentials.consumer_key, consumerSecret: credentials.consumer_secret, accessToken: credentials.access_token, accessTokenSecret: credentials.access_token_secret })) {
    return { ok: false, error: "likyly:integration gave incomplete credentials" };
  }
  return { ok: true, storeUrl, storeCode, credentials };
}

/**
 * Both steps at once (no plan check between them): the store ready and its products read, then
 * for a store LIKYLY reads itself the Integration's credentials. `integration: false` stops once
 * the module is enabled (a store already connected to LIKYLY).
 */
export async function createMagentoIntegration({ dir, cli, run, onStep = () => {}, onLine = () => {}, integration = true, apiUrl = API_URL }) {
  const prepared = await prepareMagentoStore({ dir, cli, run, onStep, onLine, apiUrl, catalog: integration });
  if (!prepared.ok || !integration || prepared.mode === "push") return prepared;
  const made = await createIntegration({ cli, run, onStep });
  return made.ok ? { ...prepared, ...made, mode: "pull" } : made;
}

// --- The plan: does the store fit? ------------------------------------------------------------

/**
 * Room left for products in the account (GET /analytics/plan): { plan, planName, room, limit,
 * upgradeUrl }, room null when the plan has no cap; null when LIKYLY can't say. `inCatalog`: items
 * the target catalog already has (sent again, they take no new room).
 */
export async function productRoom({ apiKey, inCatalog = 0, fetchImpl = fetch }) {
  try {
    const res = await fetchImpl(`${API_URL}/analytics/plan`, { headers: { "X-API-Key": apiKey }, signal: AbortSignal.timeout(20000) });
    if (!res.ok) return null;
    const data = await res.json();
    const limit = data.usage?.product_limit ?? data.limits?.product_limit ?? null;
    const used = data.usage?.product_count ?? 0;
    return { plan: data.plan, planName: data.plan_name ?? data.plan, limit, room: limit === null ? null : Math.max(0, limit - used + inCatalog), upgradeUrl: data.upgrade_url };
  } catch {
    return null;
  }
}

/** [{ name, count }] - how many products each category holds, biggest first. */
export function categoryCounts(items) {
  const counts = new Map();
  for (const item of items) for (const c of new Set(item.categories ?? [])) counts.set(c, (counts.get(c) ?? 0) + 1);
  const none = items.filter((i) => !(i.categories ?? []).length).length;
  const list = [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return none ? [...list, { name: null, count: none }] : list;
}

/** The products in at least one of these categories (`null` = products without a category). */
export function itemsInCategories(items, names) {
  const wanted = new Set(names);
  return items.filter((i) => ((i.categories ?? []).length ? i.categories.some((c) => wanted.has(c)) : wanted.has(null)));
}

/** The JSON lines of a command's output (other lines - warnings, notices - skipped). */
function jsonLines(lines) {
  const out = [];
  for (const l of lines) {
    if (!l.trim().startsWith("{")) continue;
    try {
      out.push(JSON.parse(l));
    } catch {
      // a line cut by the terminal: skipped
    }
  }
  return out;
}
