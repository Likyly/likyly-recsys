<?php

declare(strict_types=1);

namespace Likyly;

use GuzzleHttp\Client as GuzzleClient;
use GuzzleHttp\ClientInterface;
use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Resource\Events;
use Likyly\Resource\Items;
use Likyly\Resource\Recommendations;
use Likyly\Resource\Users;

/**
 * The LIKYLY client. Four things to know:
 *
 *   $likyly->items()            your catalog
 *   $likyly->users()            your users (optional)
 *   $likyly->events()           what visitors do
 *   $likyly->recommendations()  what to show them
 *
 * Two API keys: the **secret** key (server-side only: items, users, everything) and the **public** key
 * (recommendations and event tracking only). Never expose the secret key to a browser.
 */
final class LikylyClient
{
    private readonly Items $items;
    private readonly Users $users;
    private readonly Events $events;
    private readonly Recommendations $recommendations;

    /**
     * @param string|null $baseUrl defaults to https://api.likyly.com
     * @param string|null $catalog which of your catalogs to use, if your account has several (omit it if you have one)
     * @param float $timeout per-request timeout in seconds
     * @param int $maxRetries automatic retries on transient failures - 429, 502, 503, 504, dropped connections (0 disables)
     * @param string|null $userAgent appended to the SDK's User-Agent, e.g. `my-shop/1.4`
     * @param ClientInterface|null $httpClient your own Guzzle client (proxy, custom handler, tests)
     * @param callable(float): void|null $sleep test hook
     * @param callable(): float|null $random test hook
     */
    public function __construct(
        string $apiKey,
        ?string $baseUrl = null,
        ?string $catalog = null,
        float $timeout = 10.0,
        int $maxRetries = 2,
        ?string $userAgent = null,
        ?ClientInterface $httpClient = null,
        ?callable $sleep = null,
        ?callable $random = null,
    ) {
        if (trim($apiKey) === '') {
            throw new ValidationException('apiKey is required (create one in your LIKYLY account)');
        }
        $http = new HttpClient(
            $httpClient ?? new GuzzleClient(),
            $apiKey,
            rtrim($baseUrl ?? Version::DEFAULT_BASE_URL, '/'),
            $catalog,
            $timeout,
            $maxRetries,
            $userAgent !== null ? Version::USER_AGENT . ' ' . $userAgent : Version::USER_AGENT,
            $sleep,
            $random,
        );
        $this->items = new Items($http);
        $this->users = new Users($http);
        $this->events = new Events($http);
        $this->recommendations = new Recommendations($http);
    }

    public function items(): Items
    {
        return $this->items;
    }

    public function users(): Users
    {
        return $this->users;
    }

    public function events(): Events
    {
        return $this->events;
    }

    public function recommendations(): Recommendations
    {
        return $this->recommendations;
    }
}
