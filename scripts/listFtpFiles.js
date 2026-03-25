#!/usr/bin/env node
/**
 * listFtpFiles.js – List files on the FTP server.
 *
 * Usage:
 *   node scripts/listFtpFiles.js [--remote /path/on/ftp]
 *
 * Reads from .env in repo root.
 */

"use strict";

require("dotenv").config();

const ftp = require("basic-ftp");

const {
  FTP_HOST = "127.0.0.1",
  FTP_PORT = "21",
  FTP_USER = "ftpbridge",
  FTP_PASS = "",
  FTP_TLS = "false",
  FTP_UPLOAD_DIR = "/home/ftpbridge/files",
} = process.env;

const args = process.argv.slice(2);
const remoteIdx = args.indexOf("--remote");
const remotePath = remoteIdx !== -1 ? args[remoteIdx + 1] : FTP_UPLOAD_DIR;

(async () => {
  const client = new ftp.Client(15000);
  try {
    await client.access({
      host: FTP_HOST,
      port: Number(FTP_PORT),
      user: FTP_USER,
      password: FTP_PASS,
      secure: FTP_TLS === "true",
    });

    console.log(`\nListing: ${remotePath}\n${"─".repeat(60)}`);
    const list = await client.list(remotePath);
    for (const item of list) {
      const type = item.type === 2 ? "DIR " : "FILE";
      console.log(`${type}  ${item.size?.toString().padStart(12) ?? "           -"}  ${item.name}`);
    }
    console.log(`\n${list.length} item(s)`);
  } catch (err) {
    console.error("FTP error:", err.message);
    process.exit(1);
  } finally {
    client.close();
  }
})();
