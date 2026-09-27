import js from "@eslint/js";
import tseslint from "typescript-eslint";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  {
    // `.next*` rather than `.next`, because `next.config.ts` lets `NEXT_DIST_DIR`
    // move the build output (a running dev server and a `pnpm build` from the same
    // checkout otherwise fight over one directory). A literal `.next` here meant
    // the dev server's `.next-dev` was linted as if it were source: 1414 errors in
    // generated code, and a gate that no longer said anything about the app.
    ignores: [".next*/**", "node_modules/**", "next-env.d.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { "@next/next": nextPlugin, "react-hooks": reactHooks },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...reactHooks.configs.recommended.rules,
    },
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // A validator that narrows `unknown` needs to name `unknown`, not `any`.
      "@typescript-eslint/no-explicit-any": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
);
