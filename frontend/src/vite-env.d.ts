/// <reference types="vite/client" />

// Vite's own client types, which its project template ships and this repo never
// had. They declare the ambient modules for the things a Vite app imports for
// their side effects rather than their value — `./index.css` in main.tsx above
// all — plus `import.meta.env`.
//
// TypeScript 5 let the CSS import pass untyped. TypeScript 7 does not:
//
//   src/main.tsx(4,8): error TS2882: Cannot find module or type declarations
//                      for side-effect import of './index.css'.
//
// So this is the missing file rather than an accommodation to the new compiler.
// `tsc -b` runs inside `npm run build`, not only `npm run typecheck`, so without
// it the TypeScript 7 upgrade fails the build outright.
