<?php

declare(strict_types=1);

namespace Likyly\Model;

final class Explanation
{
    /**
     * @param string[]|null $sourceItemIds
     * @param SimilarUser[]|null $similarUsers only with `debug` and a secret key
     */
    public function __construct(
        public readonly string $reason,
        public readonly ?float $contentSimilarity = null,
        public readonly ?float $semanticSimilarity = null,
        public readonly ?float $popularityScore = null,
        public readonly ?int $interactionCount = null,
        public readonly ?string $interactionLabel = null,
        public readonly ?float $collaborativeScore = null,
        public readonly ?array $sourceItemIds = null,
        public readonly ?array $similarUsers = null,
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self(
            (string) $w['reason'],
            $w['content_similarity'] ?? null,
            $w['semantic_similarity'] ?? null,
            $w['popularity_score'] ?? null,
            $w['interaction_count'] ?? null,
            $w['interaction_label'] ?? null,
            $w['collaborative_score'] ?? null,
            $w['source_item_ids'] ?? null,
            isset($w['similar_users'])
                ? array_map(static fn (array $u) => new SimilarUser((string) $u['user_id'], $u['shared_item_ids'] ?? []), $w['similar_users'])
                : null,
        );
    }
}
