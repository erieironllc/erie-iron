import js from "@eslint/js";
import globals from "globals";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactPlugin from "eslint-plugin-react";

export default [
    // JavaScript and JSX files
    {
        files: ["**/*.js", "**/*.jsx"],
        ignores: [],        // REQUIRED so eslint doesn't auto-ignore outside paths

        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
            globals: {
                ...globals.browser,
                ...globals.node,
            },
            parserOptions: {
                ecmaFeatures: {
                    jsx: true,
                },
            },
        },
        plugins: {
            react: reactPlugin,
        },
        rules: {
            ...js.configs.recommended.rules,
            "no-unused-vars": "error",
            "no-undef": "error",
        },
    },

    // TypeScript and TSX files
    {
        files: ["**/*.ts", "**/*.tsx"],
        ignores: [],        // REQUIRED so eslint doesn't auto-ignore outside paths

        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
            parser: tsParser,
            parserOptions: {
                ecmaFeatures: {
                    jsx: true,
                },
            },
            globals: {
                ...globals.browser,
                ...globals.node,
            },
        },
        plugins: {
            "@typescript-eslint": tsPlugin,
            react: reactPlugin,
        },
        rules: {
            ...tsPlugin.configs.recommended.rules,
            "@typescript-eslint/no-unused-vars": "error",
            "@typescript-eslint/no-explicit-any": "warn",
        },
    },
];