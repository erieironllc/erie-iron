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
                ...globals.jest,
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
            "no-unused-vars": "warn",
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
            "@typescript-eslint/no-unused-vars": "warn",
            "@typescript-eslint/no-explicit-any": "warn",
        },
    },

    // Jest test files - JavaScript
    {
        files: [
            "**/*.test.js",
            "**/*.spec.js",
            "**/*.test.jsx",
            "**/*.spec.jsx",
            "**/__tests__/**/*.js",
            "**/__tests__/**/*.jsx",
        ],
        ignores: [],

        languageOptions: {
            globals: {
                ...globals.jest,
            },
        },
    },

    // Jest test files - TypeScript
    {
        files: [
            "**/*.test.ts",
            "**/*.spec.ts",
            "**/*.test.tsx",
            "**/*.spec.tsx",
            "**/__tests__/**/*.ts",
            "**/__tests__/**/*.tsx",
        ],
        ignores: [],

        languageOptions: {
            globals: {
                ...globals.jest,
            },
        },
    },
];