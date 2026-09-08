import js from "@eslint/js";
import globals from "globals";

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
    rules: {
      ...js.configs.recommended.rules,
    },
  },
];
