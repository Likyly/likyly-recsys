/*
 * LIKYLY for a Shopify store without the LIKYLY app: the add to cart and the purchases.
 * Served by the LIKYLY API at /pixel/shopify.js and loaded by the few lines the merchant pastes
 * in Shopify admin > Settings > Customer events > Add custom pixel:
 *
 *   const q = [];
 *   analytics.subscribe("product_added_to_cart", (e) => q.push(e));
 *   analytics.subscribe("checkout_completed", (e) => q.push(e));
 *   const s = document.createElement("script");
 *   s.src = "https://api.likyly.com/pixel/shopify.js";
 *   s.onload = () => Likyly.pixel("pk_...", { analytics, browser, init, queue: q });
 *   document.head.appendChild(s);
 *
 * The queue keeps what Shopify published before this file arrived: no order is lost. The two
 * subscribe calls stay written out with their event names: Shopify reads the pasted code to
 * know which events the pixel listens to (a loop over the names reads as "none").
 * Each purchase carries the recommendation the visitor clicked when the LIKYLY block kept it
 * (localStorage likyly_rec); otherwise LIKYLY finds the visitor's last click on the product.
 * Sent: product ids, quantities, prices, the order id, the anonymous visitor id of the LIKYLY
 * blocks and the Shopify customer id when signed in. Never a name, an email or an address.
 */
(function (root) {
  "use strict";
  var API = "https://api.likyly.com";
  var DAY = 24 * 3600 * 1000;

  function numericId(value) {
    var match = String(value == null ? "" : value).match(/(\d+)$/);
    return match ? match[1] : null;
  }

  function pixel(key, shopify) {
    var browser = shopify.browser;
    var init = shopify.init;
    var api = shopify.api || API;

    function stored(name) {
      try {
        return Promise.resolve(browser.localStorage.getItem(name)).catch(function () { return null; });
      } catch (error) {
        return Promise.resolve(null);
      }
    }

    // The visitor: the anonymous id of the LIKYLY blocks, and the customer when signed in.
    function who(orderId) {
      return stored("likyly_anonymous_id").then(function (session) {
        var out = {};
        if (session) out.session_id = session;
        var customer = numericId(init && init.data && init.data.customer && init.data.customer.id);
        if (customer) out.user_id = customer;
        if (!out.session_id && !out.user_id && orderId) out.session_id = "shopify_order_" + orderId;
        return out;
      });
    }

    // The recommendation this product was clicked in (30 days), kept by the LIKYLY block.
    function recommendations() {
      return stored("likyly_rec").then(function (raw) {
        try {
          return JSON.parse(raw || "{}") || {};
        } catch (error) {
          return {};
        }
      });
    }
    function attribution(kept, productId) {
      var entry = kept[productId];
      if (!Array.isArray(entry) || !entry[0] || Date.now() - Number(entry[2] || 0) > 30 * DAY) return {};
      return entry[1] ? { recommendation_id: String(entry[0]), placement: String(entry[1]) } : { recommendation_id: String(entry[0]) };
    }

    function send(events) {
      if (!events.length) return;
      fetch(api + "/events/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": key, "X-Likyly-Client": "likyly-shopify-pixel/1.0" },
        body: JSON.stringify({ events: events }),
        keepalive: true,
      }).catch(function () {});
    }

    function addToCart(event) {
      var line = event.data && event.data.cartLine;
      var productId = numericId(line && line.merchandise && line.merchandise.product && line.merchandise.product.id);
      if (!productId) return;
      var price = Number(line.merchandise.price && line.merchandise.price.amount);
      Promise.all([who(), recommendations()]).then(function (r) {
        send([Object.assign({
          event_type: "add_to_cart",
          item_id: productId,
          quantity: Math.max(1, Number(line.quantity) || 1),
          event_id: "cart_" + event.id,
          occurred_at: event.timestamp,
        }, r[0], attribution(r[1], productId), isFinite(price) ? { properties: { price: price, currency: line.merchandise.price.currencyCode } } : {})]);
      });
    }

    // One purchase per product of the order, with its price: what LIKYLY's revenue adds up.
    function purchase(event) {
      var checkout = event.data && event.data.checkout;
      if (!checkout) return;
      var orderId = numericId(checkout.order && checkout.order.id) || String(checkout.token || event.id);
      var byProduct = {};
      (checkout.lineItems || []).forEach(function (line) {
        var productId = numericId(line.variant && line.variant.product && line.variant.product.id);
        if (!productId) return;
        var quantity = Math.max(1, Number(line.quantity) || 1);
        var total = Number(line.finalLinePrice ? line.finalLinePrice.amount : Number(line.variant.price.amount) * quantity);
        var sum = byProduct[productId] || { quantity: 0, revenue: 0 };
        sum.quantity += quantity;
        sum.revenue += isFinite(total) ? total : 0;
        byProduct[productId] = sum;
      });
      Promise.all([who(orderId), recommendations()]).then(function (r) {
        send(Object.keys(byProduct).map(function (productId) {
          var sum = byProduct[productId];
          return Object.assign({
            event_type: "purchase",
            item_id: productId,
            quantity: sum.quantity,
            // The same order seen twice (thank-you page reloaded) counts once.
            event_id: "purchase_" + orderId + "_" + productId,
            occurred_at: event.timestamp,
            properties: {
              order_id: orderId,
              price: Math.round((sum.revenue / sum.quantity) * 100) / 100,
              revenue: Math.round(sum.revenue * 100) / 100,
              currency: checkout.currencyCode,
            },
          }, r[0], attribution(r[1], productId));
        }));
      });
    }

    function handle(event) {
      if (!event) return;
      if (event.name === "product_added_to_cart") addToCart(event);
      else if (event.name === "checkout_completed") purchase(event);
    }

    // What arrived before this file, then everything after: the queue now hands events over.
    var queue = shopify.queue || [];
    var waiting = queue.splice(0, queue.length);
    queue.push = function () {
      for (var i = 0; i < arguments.length; i++) handle(arguments[i]);
      return 0;
    };
    waiting.forEach(handle);
    if (!shopify.queue && shopify.analytics) {
      shopify.analytics.subscribe("product_added_to_cart", handle);
      shopify.analytics.subscribe("checkout_completed", handle);
    }
  }

  root.Likyly = root.Likyly || {};
  root.Likyly.pixel = pixel;
})(typeof self !== "undefined" ? self : globalThis);
