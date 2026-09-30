<?php

declare(strict_types=1);

namespace Likyly\Tests;

use GuzzleHttp\Client;
use GuzzleHttp\Handler\MockHandler;
use GuzzleHttp\HandlerStack;
use GuzzleHttp\Middleware;
use GuzzleHttp\Psr7\Response;
use Likyly\LikylyClient;
use Psr\Http\Message\RequestInterface;

abstract class TestCase extends \PHPUnit\Framework\TestCase
{
    /** @var array<int, array{request: RequestInterface}> */
    protected array $history = [];
    /** @var float[] */
    protected array $sleeps = [];

    /**
     * @param array<int, Response|\Throwable> $script replayed in order
     * @param array<string, mixed> $options extra LikylyClient constructor arguments
     */
    protected function client(array $script, array $options = []): LikylyClient
    {
        $this->history = [];
        $this->sleeps = [];
        $stack = HandlerStack::create(new MockHandler($script));
        $stack->push(Middleware::history($this->history));

        return new LikylyClient(...($options + [
            'apiKey' => 'sk_test_conformance',
            'baseUrl' => 'https://api.example.test',
            'httpClient' => new Client(['handler' => $stack]),
            'sleep' => function (float $s): void {
                $this->sleeps[] = $s;
            },
            'random' => static fn (): float => 1.0,
        ]));
    }

    /** @param array<string, mixed>|list<mixed>|null $body */
    protected static function json(int $status, array|null $body, array $headers = []): Response
    {
        return new Response($status, $headers + ['Content-Type' => 'application/json'], $body === null ? null : json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION));
    }

    protected function request(int $i = 0): RequestInterface
    {
        return $this->history[$i]['request'];
    }
}
