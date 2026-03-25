"use strict";

require("dotenv").config();

const express = require("express");
const rateLimit = require("express-rate-limit");
const logger = require("./logger");
const { handleGeneric, handleShopify, handleGitHub } = require("./webhooks");

const app = express();
const PORT = process.env.NODE_PORT || 3000;
const HOST = process.env.NODE_HOST || "0.0.0.0";

// ── Raw body capture (needed for HMAC verification) ──────────────────────────
app.use(
  express.json({
    verify: (req, _res, buf) => {
      req.rawBody = buf;
    },
    limit: "10mb",
  })
);

// ── Rate limiting ─────────────────────────────────────────────────────────────

const webhookLimiter = rateLimit({
  windowMs: 60 * 1000,   // 1 minute
  max: 100,              // max 100 requests per minute per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests, please try again later." },
});

// ── Routes ────────────────────────────────────────────────────────────────────

app.get("/health", (_req, res) => res.json({ status: "ok", service: "node-bridge" }));
app.get("/", (_req, res) => res.json({ message: "FTP Bridge – Node is running" }));

app.post("/webhook/generic", webhookLimiter, (req, res) => {
  handleGeneric(req, res).catch((err) => {
    logger.error(`Unhandled error in /webhook/generic: ${err.message}`);
    res.status(500).json({ error: "Internal server error" });
  });
});

app.post("/webhook/shopify", webhookLimiter, (req, res) => {
  handleShopify(req, res).catch((err) => {
    logger.error(`Unhandled error in /webhook/shopify: ${err.message}`);
    res.status(500).json({ error: "Internal server error" });
  });
});

app.post("/webhook/github", webhookLimiter, (req, res) => {
  handleGitHub(req, res).catch((err) => {
    logger.error(`Unhandled error in /webhook/github: ${err.message}`);
    res.status(500).json({ error: "Internal server error" });
  });
});

// ── 404 fallback ──────────────────────────────────────────────────────────────
app.use((_req, res) => res.status(404).json({ error: "Not found" }));

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, HOST, () => {
  logger.info(`FTP Bridge (Node) listening on http://${HOST}:${PORT}`);
});

module.exports = app;
