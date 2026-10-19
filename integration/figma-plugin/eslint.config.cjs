const eslint = require("@eslint/js");
const tseslint = require("typescript-eslint");
const figmaPlugin = require("@figma/eslint-plugin-figma-plugins");

module.exports = tseslint.config(
  eslint.configs.recommended,
  tseslint.configs.recommended,
  {
    languageOptions: { parserOptions: { project: "./tsconfig.json", tsconfigRootDir: __dirname } },
    plugins: { "@figma/figma-plugins": figmaPlugin },
    rules: {
      ...figmaPlugin.configs.recommended.rules,
      "no-empty": ["error", { allowEmptyCatch: true }],
      "@typescript-eslint/no-unused-vars": ["error", { caughtErrors: "none" }],
    },
  },
);
