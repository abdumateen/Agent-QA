const { compilerOptions } = require("./tsconfig.json");

/** @type {import("jest").Config} */
module.exports = {
  rootDir: __dirname,
  testEnvironment: "node",
  testMatch: ["<rootDir>/tests/**/*.test.ts"],
  moduleFileExtensions: ["ts", "js", "json"],
  transform: {
    "^.+\\.ts$": [
      "ts-jest",
      {
        tsconfig: {
          ...compilerOptions,
          rootDir: __dirname,
          types: ["node", "jest"],
          declaration: false
        },
        diagnostics: {
          warnOnly: false
        }
      }
    ]
  },
  clearMocks: true,
  restoreMocks: true,
  resetModules: false,
  collectCoverageFrom: [
    "src/**/*.ts",
    "!src/**/*.d.ts"
  ],
  coverageDirectory: "<rootDir>/coverage",
  coverageProvider: "v8",
  coverageReporters: ["text", "lcov", "json-summary"],
  testTimeout: 10000
};