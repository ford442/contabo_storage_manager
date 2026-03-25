"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const ftp = require("basic-ftp");
const logger = require("./logger");

const {
  WEBHOOK_SECRET,
  WEBHOOK_HMAC_ALGO = "sha256",
  FILES_DIR = "/data/files",
  FTP_HOST = "127.0.0.1",
  FTP_PORT = "21",
  FTP_USER = "ftpbridge",
  FTP_PASS = "",
  FTP_TLS = "false",
  FTP_UPLOAD_DIR = "/home/ftpbridge/files",
} = process.env;

// ── Signature verification ────────────────────────────────────────────────────

function verifySignature(rawBody, signatureHeader, res) {
  if (!WEBHOOK_SECRET) return true; // verification disabled

  if (!signatureHeader) {
    res.status(401).json({ error: "Missing signature header" });
    return false;
  }

  const provided = signatureHeader.split("=").pop();
  const expected = crypto
    .createHmac(WEBHOOK_HMAC_ALGO, WEBHOOK_SECRET)
    .update(rawBody)
    .digest("hex");

  if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(provided))) {
    res.status(401).json({ error: "Invalid webhook signature" });
    return false;
  }
  return true;
}

// ── FTP upload helper ─────────────────────────────────────────────────────────

async function uploadToFtp(localFilePath, remotePath) {
  const client = new ftp.Client(15000);
  try {
    await client.access({
      host: FTP_HOST,
      port: Number(FTP_PORT),
      user: FTP_USER,
      password: FTP_PASS,
      secure: FTP_TLS === "true",
    });
    const remoteDir = path.dirname(remotePath);
    await client.ensureDir(remoteDir);
    await client.uploadFrom(localFilePath, remotePath);
    logger.info(`FTP upload OK → ${remotePath}`);
  } catch (err) {
    logger.warn(`FTP upload failed (file still saved locally): ${err.message}`);
  } finally {
    client.close();
  }
}

// ── Path-safe name helper ─────────────────────────────────────────────────────

function safeName(value, maxLen = 64) {
  return value.replace(/[^\w\-]/g, "_").slice(0, maxLen);
}

// ── Persistence helper ────────────────────────────────────────────────────────

async function savePayload(source, event, rawBody) {
  const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15); // YYYYMMDDHHmmss
  const safeSource = safeName(source);
  const safeEvent = safeName(event.replace(/\./g, "_"));
  const filename = `${safeSource}_${safeEvent}_${ts}.json`;
  const relPath = path.join("webhooks", safeSource, filename);

  const localPath = path.join(FILES_DIR, relPath);
  fs.mkdirSync(path.dirname(localPath), { recursive: true });
  fs.writeFileSync(localPath, rawBody);
  logger.info(`Saved webhook payload → ${localPath}`);

  const remotePath = path.posix.join(FTP_UPLOAD_DIR, relPath.replace(/\\/g, "/"));
  await uploadToFtp(localPath, remotePath);

  return relPath;
}

// ── Route handlers ────────────────────────────────────────────────────────────

async function handleGeneric(req, res) {
  const rawBody = req.rawBody;
  if (!verifySignature(rawBody, req.headers["x-hub-signature-256"], res)) return;

  let data;
  try {
    data = JSON.parse(rawBody);
  } catch {
    return res.status(422).json({ error: "Invalid JSON" });
  }

  const { source = "unknown", event = "unknown" } = data;
  const relPath = await savePayload(source, event, rawBody);
  res.json({ status: "ok", message: "Payload received", file: relPath });
}

async function handleShopify(req, res) {
  const rawBody = req.rawBody;
  if (!verifySignature(rawBody, req.headers["x-shopify-hmac-sha256"], res)) return;

  const event = req.headers["x-shopify-topic"] || "unknown.event";

  let data;
  try {
    data = JSON.parse(rawBody);
  } catch {
    return res.status(422).json({ error: "Invalid JSON" });
  }

  const relPath = await savePayload("shopify", event, rawBody);
  res.json({ status: "ok", message: "Shopify payload received", file: relPath });
}

async function handleGitHub(req, res) {
  const rawBody = req.rawBody;
  if (!verifySignature(rawBody, req.headers["x-hub-signature-256"], res)) return;

  const event = req.headers["x-github-event"] || "unknown";

  let data;
  try {
    data = JSON.parse(rawBody);
  } catch {
    return res.status(422).json({ error: "Invalid JSON" });
  }

  const relPath = await savePayload("github", event, rawBody);
  res.json({ status: "ok", message: "GitHub payload received", file: relPath });
}

module.exports = { handleGeneric, handleShopify, handleGitHub };
