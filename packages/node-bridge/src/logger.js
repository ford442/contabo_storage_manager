"use strict";

const winston = require("winston");

const { LOG_LEVEL = "info", LOG_FILE = "/var/log/ftpbridge/app.log" } = process.env;

const transports = [
  new winston.transports.Console({
    format: winston.format.combine(
      winston.format.timestamp({ format: "YYYY-MM-DDTHH:mm:ss" }),
      winston.format.printf(({ timestamp, level, message }) => `${timestamp} ${level.toUpperCase().padEnd(8)} [node-bridge] ${message}`)
    ),
  }),
];

try {
  const fs = require("fs");
  const path = require("path");
  fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
  transports.push(
    new winston.transports.File({
      filename: LOG_FILE,
      format: winston.format.combine(winston.format.timestamp(), winston.format.json()),
    })
  );
} catch {
  // File logging unavailable – stdout only
}

const logger = winston.createLogger({ level: LOG_LEVEL, transports });

module.exports = logger;
