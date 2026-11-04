// Clipboard and browser helpers. Best effort: they return false instead of throwing when the
// platform tool is missing (headless Linux, SSH...), and the UI says so.

import { spawn, spawnSync } from "node:child_process";

const CLIPBOARD = {
  darwin: [["pbcopy", []]],
  win32: [["clip", []]],
  linux: [
    ["wl-copy", []],
    ["xclip", ["-selection", "clipboard"]],
    ["xsel", ["--clipboard", "--input"]],
  ],
};

export function copyToClipboard(text) {
  for (const [cmd, args] of CLIPBOARD[process.platform] ?? CLIPBOARD.linux) {
    try {
      const res = spawnSync(cmd, args, { input: text, stdio: ["pipe", "ignore", "ignore"] });
      if (res.status === 0) return true;
    } catch {
      /* try the next tool */
    }
  }
  return false;
}

export function openUrl(url) {
  // BROWSER=none (the create-react-app convention): never open a browser, print the link only.
  if (process.env.BROWSER === "none") return false;
  const [cmd, args] =
    process.platform === "darwin" ? ["open", [url]] : process.platform === "win32" ? ["cmd", ["/c", "start", "", url]] : ["xdg-open", [url]];
  try {
    const child = spawn(cmd, args, { stdio: "ignore", detached: true });
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}
