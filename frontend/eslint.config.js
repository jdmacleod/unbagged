// ESLint for the UI (CODING_STANDARDS.md, TypeScript and React). Type-aware:
// typescript-eslint reads tsconfig.json through the project service. Findings that
// predate these rules are held in eslint-suppressions.json, which may only shrink;
// `npm run lint` fails on anything new, warnings included.
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import importX from "eslint-plugin-import-x";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "node_modules", "coverage"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat.recommended,
      jsxA11y.flatConfigs.recommended,
    ],
    plugins: { "import-x": importX },
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "import-x/order": [
        "error",
        {
          groups: ["builtin", "external", "internal", "parent", "sibling", "index"],
          "newlines-between": "never",
          alphabetize: { order: "asc", caseInsensitive: true },
        },
      ],
      // `onClick={() => setOpen(false)}` is how React handlers are written; the
      // strict default would wrap every one of them in braces.
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // Counts and prices in messages are numbers; every other type still needs String().
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      // Size and nesting (CODING_STANDARDS.md, size and complexity). Components count.
      complexity: ["error", 15],
      "max-lines-per-function": ["error", { max: 120, skipBlankLines: true, skipComments: true }],
      "max-depth": ["error", 4],
    },
  },
  // Last, so it turns off every stylistic rule Prettier owns.
  prettier,
]);
