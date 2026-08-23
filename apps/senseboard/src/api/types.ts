/**
 * Re-export of the frozen contracts TS mirror so application code imports from
 * one place (`@/api/types`). Nothing is redefined here: if a field is missing,
 * the contracts package is the thing to fix, not this file.
 */
export * from "@contracts/types";
