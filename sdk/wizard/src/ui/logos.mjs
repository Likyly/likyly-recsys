// A small mark of the detected platform, in its colours, so the developer sees at a glance that
// the wizard recognised their stack (shown on the plan card). Drawn with block characters,
// 4-5 lines high; stacks without a drawing get a badge with their initials.

const MARKS = {
  magento: {
    color: "#F26322",
    art: ["   ▄█▄   ", " ▄█▀ ▀█▄ ", " █  ▄  █ ", " █ █▀█ █ ", " ▀ ▀ ▀ ▀ "],
  },
  shopify: {
    color: "#95BF47",
    art: ["  ▄▀▀▀▀▄  ", " ██▀▀▀▀██ ", " ██ ▀▀▀██ ", " ██▀▀▀ ██ ", " ▀▀▀▀▀▀▀▀ "],
  },
  wordpress: {
    color: "#3858E9",
    art: [" ▄▀▀▀▀▀▀▀▄ ", "█ █  █  █ █", "█ ▀▄▀ ▀▄▀ █", " ▀▄▄▄▄▄▄▄▀ "],
  },
  drupal: {
    color: "#0678BE",
    art: ["    ▄    ", "   ███   ", "  █████  ", " ██▀█▀██ ", "  ▀▄▄▄▀  "],
  },
  nextjs: {
    color: "#FFFFFF",
    art: [" ▄▀▀▀▀▀▄ ", "█ █▄  █ █", "█ █ ▀▄█ █", " ▀▄▄▄▄▄▀ "],
  },
  vue: {
    color: "#42B883",
    art: ["█▀▄   ▄▀█", " ▀▄▀▄▀▄▀ ", "  ▀▄█▄▀  ", "    ▀    "],
  },
  nuxt: {
    color: "#00DC82",
    art: ["   ▄▀▄     ", "  ▄▀ ▀▄▄▀▄ ", " ▄▀   ▄▀ ▀▄", " ▀▀▀▀▀▀▀▀▀▀"],
  },
  react: {
    color: "#61DAFB",
    art: [" ▄▀▀▄▄▀▀▄ ", "▀▄ ▄██▄ ▄▀", "▄▀ ▀██▀ ▀▄", " ▀▄▄▀▀▄▄▀ "],
  },
  angular: {
    color: "#DD0031",
    art: [" ▄▄█▀▀█▄▄ ", " █  ▄▄  █ ", " ▀▄ █▀█ ▄▀", "   ▀▄▄▀   "],
  },
};
// Same mark for the variants of a stack.
const ALIAS = { "react-vite": "react", "react-cra": "react", remix: "react", "shopify-app": "shopify" };

// Initials and colour of the stacks without a drawing.
const BADGES = {
  laravel: ["Lv", "#FF2D20"],
  symfony: ["sf", "#FFFFFF"],
  php: ["php", "#777BB4"],
  astro: ["A", "#FF5D01"],
  sveltekit: ["S", "#FF3E00"],
  django: ["dj", "#44B78B"],
  flask: ["Fl", "#FFFFFF"],
  fastapi: ["Fa", "#009688"],
  python: ["Py", "#3776AB"],
  rails: ["Rb", "#CC0000"],
  ruby: ["Rb", "#CC342D"],
  node: ["JS", "#5FA04E"],
  javascript: ["JS", "#F7DF1E"],
  go: ["Go", "#00ADD8"],
  rust: ["Rs", "#DEA584"],
  java: ["Jv", "#E76F00"],
  dotnet: [".N", "#512BD4"],
};

/** `{ color, art: string[] }` for a detected project, or null when nothing fits. */
export function logoFor(project) {
  if (!project) return null;
  const id = ALIAS[project.id] ?? project.id;
  if (MARKS[id]) return MARKS[id];
  const [initials, color] = BADGES[id] ?? [];
  if (!initials) return null;
  const inner = ` ${initials} `;
  const bar = "─".repeat(Math.max(inner.length, 4));
  return { color, art: [`╭${bar}╮`, `│${inner.padEnd(bar.length)}│`, `╰${bar}╯`] };
}
