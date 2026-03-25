"use strict";

/**
 * Shared FTP utilities (Node.js) using basic-ftp.
 * Usage:
 *   const { uploadBuffer } = require("../../shared/ftp/ftpUtils");
 *   await uploadBuffer(Buffer.from("hello"), "/remote/path/file.txt");
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const ftp = require("basic-ftp");

const {
  FTP_HOST = "127.0.0.1",
  FTP_PORT = "21",
  FTP_USER = "ftpbridge",
  FTP_PASS = "",
  FTP_TLS = "false",
} = process.env;

/**
 * Upload a Buffer to the FTP server.
 * @param {Buffer} buffer
 * @param {string} remotePath  Absolute remote path
 */
async function uploadBuffer(buffer, remotePath) {
  // basic-ftp requires a file path, so write to a temp file first
  const tmp = path.join(os.tmpdir(), `ftpbridge-${Date.now()}`);
  fs.writeFileSync(tmp, buffer);
  try {
    await uploadLocalFile(tmp, remotePath);
  } finally {
    fs.unlinkSync(tmp);
  }
}

/**
 * Upload a local file to the FTP server.
 * @param {string} localPath
 * @param {string} remotePath  Absolute remote path
 */
async function uploadLocalFile(localPath, remotePath) {
  const client = new ftp.Client(15000);
  try {
    await client.access({
      host: FTP_HOST,
      port: Number(FTP_PORT),
      user: FTP_USER,
      password: FTP_PASS,
      secure: FTP_TLS === "true",
    });
    await client.ensureDir(path.dirname(remotePath));
    await client.uploadFrom(localPath, remotePath);
  } finally {
    client.close();
  }
}

module.exports = { uploadBuffer, uploadLocalFile };
