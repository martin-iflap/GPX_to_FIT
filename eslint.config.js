import js from "@eslint/js";
import globals from "globals";

const rules = {
  ...js.configs.recommended.rules,
  // An inner name that hides an outer one reads as the outer one.
  "no-shadow": "error",
  // `== null` is the one loose comparison kept: it means null or undefined.
  eqeqeq: ["error", "always", { null: "ignore" }],
  "prefer-const": "error",
};

export default [
  {
    ignores: ["src/gpx2fit/gui/js/config.js"],
  },
  {
    files: ["src/gpx2fit/gui/js/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.browser,
    },
    rules,
  },
  {
    // The tests run in Node, with jsdom's browser globals installed.
    files: ["tests/gui/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
    },
    rules,
  },
];
