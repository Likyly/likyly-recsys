<?php

declare(strict_types=1);

namespace Likyly\Tests;

use Likyly\Exception\LikylyException;
use PHPUnit\Framework\Attributes\DataProvider;

/**
 * Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs,
 * and that validate_against_openapi.py checks against the OpenAPI document.
 */
final class ConformanceTest extends TestCase
{
    /** @return array<string, array{0: array<string, mixed>, 1: array<string, mixed>}> */
    public static function scenarios(): array
    {
        $file = json_decode((string) file_get_contents(__DIR__ . '/../../conformance/scenarios.json'), true, 512, JSON_THROW_ON_ERROR);
        $out = [];
        foreach ($file['scenarios'] as $s) {
            $out[$s['id']] = [$s, $file['defaults']];
        }

        return $out;
    }

    /**
     * @param array<string, mixed> $scenario
     * @param array<string, mixed> $defaults
     */
    #[DataProvider('scenarios')]
    public function testScenario(array $scenario, array $defaults): void
    {
        $response = $scenario['response'];
        $options = ['maxRetries' => 0];
        if (isset($scenario['config']['catalog'])) {
            $options['catalog'] = $scenario['config']['catalog'];
        }
        $client = $this->client([self::json($response['status'], $response['body'], $response['headers'])], $options);

        $resource = $client->{$scenario['call']['resource']}();
        $method = $scenario['call']['method'];
        [$positional, $named] = self::args($scenario['call']['args'], $method);

        if (isset($scenario['error'])) {
            try {
                $resource->{$method}(...$positional, ...$named);
                $this->fail('expected an exception');
            } catch (LikylyException $e) {
                $want = $scenario['error'];
                $this->assertSame(str_replace('Error', 'Exception', $want['class']), (new \ReflectionClass($e))->getShortName());
                $this->assertSame($want['statusCode'], $e->statusCode);
                if (isset($want['requestId'])) {
                    $this->assertSame($want['requestId'], $e->requestId);
                }
                if (isset($want['retryAfter'])) {
                    $this->assertEquals($want['retryAfter'], $e->retryAfter);
                }
                if (isset($want['message'])) {
                    $this->assertSame($want['message'], $e->getMessage());
                }
            }
        } else {
            $result = $resource->{$method}(...$positional, ...$named);
            foreach ($scenario['expect'] ?? [] as $path => $want) {
                $this->assertEquals($want, self::pick($result, $path), "result.{$path}");
            }
        }

        $this->assertRequest($scenario, $defaults);
    }

    /**
     * Scenario args are JSON: option objects become named arguments (PHP's camelCase parameter names match the
     * scenario's keys); lists of entries stay one positional argument.
     *
     * @param array<int, mixed> $args
     * @return array{0: array<int, mixed>, 1: array<string, mixed>}
     */
    private static function args(array $args, string $method): array
    {
        $positional = [];
        $named = [];
        $many = in_array($method, ['upsertMany', 'import', 'trackMany'], true);
        foreach ($args as $a) {
            if (is_array($a) && !array_is_list($a) && !$many) {
                $named = $named + $a;
            } else {
                $positional[] = $a;
            }
        }

        return [$positional, $named];
    }

    private static function pick(mixed $obj, string $path): mixed
    {
        foreach (explode('.', $path) as $key) {
            if ($obj === null) {
                return null;
            }
            if ($key === 'length') {
                return count((array) $obj);
            }
            $obj = is_array($obj) ? ($obj[ctype_digit($key) ? (int) $key : $key] ?? null) : $obj->{$key};
        }

        return $obj;
    }

    /**
     * @param array<string, mixed> $scenario
     * @param array<string, mixed> $defaults
     */
    private function assertRequest(array $scenario, array $defaults): void
    {
        $this->assertCount(1, $this->history, 'exactly one HTTP request');
        $request = $this->request();
        $want = $scenario['request'];
        $uri = $request->getUri();

        $this->assertSame($want['method'], $request->getMethod());
        $this->assertSame($defaults['baseUrl'], $uri->getScheme() . '://' . $uri->getAuthority());
        $this->assertSame($want['path'], $uri->getPath(), 'raw request path');
        parse_str($uri->getQuery(), $query);
        $this->assertEquals($want['query'], $query, 'query string');
        $this->assertSame($defaults['apiKey'], $request->getHeaderLine('X-API-Key'));
        $this->assertStringStartsWith($defaults['userAgentPrefix'], $request->getHeaderLine('User-Agent'));
        $this->assertSame(array_key_exists('body', $want) ? 'application/json' : '', $request->getHeaderLine('Content-Type'));

        $raw = (string) $request->getBody();
        if (array_key_exists('body', $want)) {
            $this->assertEquals($want['body'], json_decode($raw, true), 'request body');
            if ($want['body'] === []) {
                $this->assertSame('{}', $raw, 'an empty body is a JSON object, not a list');
            }
        } else {
            $this->assertSame('', $raw);
        }
    }
}
