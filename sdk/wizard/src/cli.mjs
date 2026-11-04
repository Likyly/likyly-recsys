// `npx @likyly/wizard` - detect the framework, install the SDK, write the public key, connect
// the developer's AI coding tool to the LIKYLY MCP server, and hand over to it with a prompt.

import { createInterface } from "node:readline/promises";
import { readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { stdin, stdout } from "node:process";
import { EDITORS, detectEditors, detectProject } from "./frameworks.mjs";
import { KEY_ENV, writeMcpConfig } from "./mcp.mjs";
import { commandExists, isServerOnlyKey, run, writeEnv, writeSkill, writeStarter } from "./setup.mjs";

const KEYS_URL = "https://www.likyly.com/account/keys";
const DOCS_URL = "https://www.likyly.com/docs/installation";
const WP_PLUGIN_URL = "https://www.likyly.com/docs/installation#wordpress";
const AGENT_PROMPT =
  "Utilise la skill likyly et le serveur MCP LIKYLY : connecte mon catalogue, crée un placement “Vous aimerez aussi” sur ma page produit, intègre-le dans mon code, puis valide l'intégration.";

const color = !process.env.NO_COLOR && stdout.isTTY;
const paint = (code) => (s) => (color ? `\x1b[${code}m${s}\x1b[0m` : String(s));
const bold = paint("1");
const dim = paint("2");
const green = paint("32");
const yellow = paint("33");
const red = paint("31");
const blue = paint("34");

const SERVER_KEY_MESSAGE =
  "Cette clé est une clé secrète ou développeur : elle ne doit jamais aller dans le navigateur. Utilisez la clé publique (pk_...).";

// Any failed step makes the run exit non-zero, so CI (`--yes`) notices it.
let failures = 0;
const ok = (msg) => console.log(`  ${green("✓")} ${msg}`);
const warn = (msg) => console.log(`  ${yellow("!")} ${msg}`);
const fail = (msg) => {
  failures++;
  console.log(`  ${red("✗")} ${msg}`);
};
const step = (n, total, title) => console.log(`\n${blue(`[${n}/${total}]`)} ${bold(title)}`);

const HELP = `
${bold("LIKYLY wizard")} - installe LIKYLY dans votre projet en une commande.

${bold("Usage")}
  npx @likyly/wizard [commande] [options]

${bold("Commandes")}
  install        (défaut) détecte le framework, installe le SDK, écrit la clé,
                 connecte votre assistant IA au serveur MCP LIKYLY
  mcp add        connecte seulement votre assistant IA (Claude Code, Cursor, VS Code)
  detect         affiche ce qui a été détecté, sans rien modifier

${bold("Options")}
  --dir <chemin>         dossier du projet (défaut : dossier courant)
  --public-key <clé>     votre clé publique LIKYLY (sinon demandée)
  --editor <ids>         claude-code,cursor,vscode (défaut : ceux détectés)
  --hosted               utilise le serveur MCP hébergé (mcp.likyly.com) au lieu de npx
  --no-install           n'exécute pas le gestionnaire de paquets
  --dry-run              montre ce qui serait fait, sans rien écrire
  -y, --yes              accepte les choix par défaut, sans question
  -h, --help             cette aide
  -v, --version          version
`;

export function parseArgs(argv) {
  const opts = { command: "install", dir: process.cwd(), yes: false, dryRun: false, install: true, hosted: false, editors: null, publicKey: null };
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${a} attend une valeur`);
      return v;
    };
    if (a === "-h" || a === "--help") opts.command = "help";
    else if (a === "-v" || a === "--version") opts.command = "version";
    else if (a === "-y" || a === "--yes") opts.yes = true;
    else if (a === "--dry-run") opts.dryRun = true;
    else if (a === "--no-install") opts.install = false;
    else if (a === "--hosted") opts.hosted = true;
    else if (a === "--dir") opts.dir = next();
    else if (a === "--public-key") opts.publicKey = next();
    else if (a === "--editor" || a === "--editors") opts.editors = next().split(",").map((s) => s.trim()).filter(Boolean);
    else if (a.startsWith("-")) throw new Error(`option inconnue : ${a}`);
    else positional.push(a);
  }
  if (opts.command !== "help" && opts.command !== "version" && positional.length) {
    const cmd = positional.join(" ");
    if (cmd === "mcp add" || cmd === "mcp") opts.command = "mcp";
    else if (cmd === "detect") opts.command = "detect";
    else if (cmd === "install") opts.command = "install";
    else throw new Error(`commande inconnue : ${cmd}`);
  }
  opts.dir = resolve(opts.dir);
  const known = EDITORS.map((e) => e.id);
  for (const e of opts.editors ?? []) if (!known.includes(e)) throw new Error(`éditeur inconnu : ${e} (attendu : ${known.join(", ")})`);
  // Refused up front, before anything is installed or written.
  if (opts.publicKey && isServerOnlyKey(opts.publicKey)) throw new Error(`--public-key : ${SERVER_KEY_MESSAGE}`);
  return opts;
}

function makePrompter(yes) {
  const interactive = !yes && stdin.isTTY;
  const rl = interactive ? createInterface({ input: stdin, output: stdout }) : null;
  return {
    interactive,
    async confirm(question, def = true) {
      if (!rl) return def;
      const a = (await rl.question(`  ${question} ${dim(def ? "[O/n]" : "[o/N]")} `)).trim().toLowerCase();
      return a === "" ? def : ["o", "oui", "y", "yes"].includes(a);
    },
    async ask(question) {
      if (!rl) return "";
      return (await rl.question(`  ${question} `)).trim();
    },
    close() {
      rl?.close();
    },
  };
}

async function chooseEditors(opts, prompt) {
  if (opts.editors) return opts.editors;
  const detected = detectEditors(opts.dir);
  if (!prompt.interactive) return detected.length ? detected : ["claude-code"];
  const chosen = [];
  for (const e of EDITORS) {
    if (await prompt.confirm(`Connecter ${bold(e.label)} ${dim(`(${e.file})`)} ?`, detected.includes(e.id) || (!detected.length && e.id === "claude-code"))) chosen.push(e.id);
  }
  return chosen;
}

function connectEditors(opts, editors) {
  for (const editor of editors) {
    try {
      const { path, replaced } = writeMcpConfig(opts.dir, editor, { hosted: opts.hosted, dryRun: opts.dryRun });
      ok(`${EDITORS.find((e) => e.id === editor).label} : ${relative(opts.dir, path)} ${dim(replaced ? "(serveur likyly mis à jour)" : "")}`);
    } catch (err) {
      fail(err.message);
    }
  }
  if (editors.includes("claude-code")) {
    const skill = writeSkill(opts.dir, { dryRun: opts.dryRun });
    if (skill) ok(`Skill LIKYLY pour Claude Code : ${relative(opts.dir, skill.path)}`);
  }
}

function printHandOff(editors) {
  console.log(`\n${bold("Dernière étape : laissez votre assistant IA finir le travail")}`);
  if (editors.length) {
    console.log(`  1. Créez une ${bold("clé développeur")} sur ${blue(KEYS_URL)} puis rendez-la disponible :`);
    console.log(`     ${dim("$")} export ${KEY_ENV}=lk_...`);
    if (editors.includes("vscode")) console.log(`     ${dim("(VS Code vous la demandera au démarrage du serveur, rien à exporter)")}`);
    console.log(`  2. Redémarrez votre assistant, puis collez ce prompt :`);
  } else {
    console.log("  Collez ce prompt dans votre assistant IA :");
  }
  console.log(`\n     ${green(`« ${AGENT_PROMPT} »`)}\n`);
  console.log(`  Documentation : ${blue(DOCS_URL)}\n`);
}

async function install(opts, prompt) {
  const project = detectProject(opts.dir);
  const total = 4;

  step(1, total, "Détection du projet");
  if (!project) {
    warn("Aucun framework reconnu ici. Lancez la commande à la racine de votre projet,");
    warn(`ou suivez l'installation manuelle : ${DOCS_URL}`);
    return 1;
  }
  ok(`${bold(project.label)} détecté${project.packageManager ? ` ${dim(`(${project.packageManager})`)}` : ""}`);

  step(2, total, "Installation du SDK");
  if (project.plugin) {
    warn("WordPress : installez l'extension Likyly Connector, puis renseignez votre clé dans Réglages > Likyly.");
    warn(`Guide : ${WP_PLUGIN_URL}`);
  } else if (project.install) {
    const line = project.install.cmd.join(" ");
    if (!project.install.run || !opts.install) {
      warn(`À lancer vous-même : ${bold(line)}`);
    } else if (opts.dryRun) {
      ok(`${dim("(dry-run)")} ${line}`);
    } else if (!commandExists(project.install.cmd[0])) {
      warn(`${project.install.cmd[0]} introuvable. À lancer vous-même : ${bold(line)}`);
    } else if (await prompt.confirm(`Lancer ${bold(line)} ?`)) {
      console.log(dim(`  $ ${line}`));
      if (run(opts.dir, project.install.cmd)) ok(`${project.packages.join(", ")} installé`);
      else fail(`L'installation a échoué. Relancez : ${line}`);
    } else {
      warn(`Ignoré. À lancer plus tard : ${line}`);
    }
  }

  step(3, total, "Clé publique et fichier de démarrage");
  if (project.envFile && project.envVar) {
    let key = opts.publicKey;
    if (!key && prompt.interactive) {
      console.log(`  Votre ${bold("clé publique")} est sur ${blue(KEYS_URL)} ${dim("(Entrée pour passer)")}`);
      key = await prompt.ask("Clé publique :");
      while (key && isServerOnlyKey(key)) {
        warn(SERVER_KEY_MESSAGE);
        key = await prompt.ask("Clé publique (Entrée pour passer) :");
      }
    }
    if (key) {
      const res = writeEnv(opts.dir, project.envFile, project.envVar, key, { dryRun: opts.dryRun });
      if (res.status === "exists") warn(`${project.envVar} est déjà dans ${project.envFile}, inchangé`);
      else ok(`${project.envVar} ajouté à ${project.envFile}`);
    } else {
      warn(`Ajoutez plus tard ${bold(`${project.envVar}=<clé publique>`)} dans ${project.envFile}`);
    }
  } else {
    warn("Ajoutez votre clé publique dans la configuration de l'application (voir la documentation).");
  }
  const starter = writeStarter(opts.dir, project.starter, { dryRun: opts.dryRun });
  if (starter?.status === "created") ok(`Fichier de démarrage : ${relative(opts.dir, starter.path)}`);
  else if (starter?.status === "exists") warn(`${relative(opts.dir, starter.path)} existe déjà, inchangé`);

  step(4, total, "Connexion de votre assistant IA (MCP)");
  const editors = await chooseEditors(opts, prompt);
  if (editors.length) connectEditors(opts, editors);
  else warn("Aucun assistant connecté (relancez plus tard : npx @likyly/wizard mcp add)");

  printHandOff(editors);
  return failures ? 1 : 0;
}

async function mcpAdd(opts, prompt) {
  console.log(`\n${bold("Connexion de votre assistant IA au serveur MCP LIKYLY")}`);
  const editors = await chooseEditors(opts, prompt);
  if (!editors.length) {
    warn("Aucun assistant choisi.");
    return 1;
  }
  connectEditors(opts, editors);
  printHandOff(editors);
  return failures ? 1 : 0;
}

export async function main(argv = process.argv.slice(2)) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    console.error(`${red("Erreur :")} ${err.message}\n${HELP}`);
    return 2;
  }
  if (opts.command === "help") {
    console.log(HELP);
    return 0;
  }
  if (opts.command === "version") {
    console.log(JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version);
    return 0;
  }
  if (opts.command === "detect") {
    console.log(JSON.stringify({ project: detectProject(opts.dir), editors: detectEditors(opts.dir) }, null, 2));
    return 0;
  }

  console.log(`\n${bold("LIKYLY")} ${dim("· des recommandations sur votre site, installées pour vous")}`);
  if (opts.dryRun) console.log(yellow("  Mode dry-run : rien ne sera écrit ni installé."));
  const prompt = makePrompter(opts.yes);
  try {
    return opts.command === "mcp" ? await mcpAdd(opts, prompt) : await install(opts, prompt);
  } finally {
    prompt.close();
  }
}
