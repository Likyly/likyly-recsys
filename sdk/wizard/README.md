# @likyly/wizard

Installe LIKYLY dans un projet en une commande, à la manière de `npx @posthog/wizard` :

```bash
npx @likyly/wizard
```

1. **Détecte le framework** à partir des fichiers du projet (`package.json`, `composer.json`,
   `pyproject.toml`, `Gemfile`, `go.mod`, `Cargo.toml`, `*.csproj`, `pom.xml`, `wp-config.php`)
   et le gestionnaire de paquets déjà utilisé (npm, pnpm, yarn, bun, composer, uv, poetry, bundler…).
2. **Installe le SDK** adapté (`@likyly/sdk` + `@likyly/react` pour les frameworks React).
   `pip` nu, Maven/Gradle et WordPress sont affichés, pas exécutés.
3. **Écrit la clé publique** dans le bon fichier, sous le nom que le framework expose au
   navigateur (`NEXT_PUBLIC_LIKYLY_KEY`, `VITE_LIKYLY_KEY`, `PUBLIC_LIKYLY_KEY`…), et un fichier
   de démarrage (provider React, plugin Nuxt, module SvelteKit…). Refuse une clé secrète
   (`sk_`) ou développeur (`lk_`). N'écrase jamais un fichier ni une variable existante.
4. **Connecte l'assistant IA** au [serveur MCP LIKYLY](../mcp) : `.mcp.json` (Claude Code, plus la
   skill dans `.claude/skills/likyly`), `.cursor/mcp.json` (Cursor), `.vscode/mcp.json` (VS Code).
   La clé développeur n'est jamais écrite dans ces fichiers : ils référencent
   `LIKYLY_DEVELOPER_KEY` (ou VS Code la demande au démarrage).
5. **Passe la main** à l'assistant avec un prompt prêt à coller : il connecte le catalogue, crée le
   placement, l'intègre dans le code et valide l'intégration via les outils MCP.

## Commandes

```bash
npx @likyly/wizard                 # installation complète (interactive)
npx @likyly/wizard mcp add         # seulement l'étape assistant IA
npx @likyly/wizard detect          # affiche la détection, ne modifie rien
npx @likyly/wizard --yes --public-key pk_... --editor claude-code,cursor   # CI / non interactif
npx @likyly/wizard --dry-run       # montre ce qui serait fait
npx @likyly/wizard --hosted        # MCP hébergé (mcp.likyly.com) au lieu de npx
```

## Développement

```bash
npm test                                         # node:test, sans dépendance
node bin/likyly-wizard.mjs --dry-run --dir ../../examples/demo-storefront
```

Aucune dépendance npm : `npx` démarre instantanément. La skill est copiée depuis
`docs/SKILL.md` au moment du `npm pack` (`prepack`) ; en local elle est lue directement depuis
le monorepo.

Avant publication : `@likyly/sdk`, `@likyly/react` et `@likyly/mcp-server` doivent être sur npm
(les commandes que le wizard lance et écrit les installent depuis le registre), et
`mcp.likyly.com` déployé pour `--hosted`.
