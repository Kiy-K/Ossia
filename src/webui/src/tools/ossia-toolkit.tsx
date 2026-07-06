/**
 * Ossia Tool UI toolkit — barrel file.
 *
 * Imports tool UI components from category-split files and assembles them
 * into the ``ossiaToolkit`` via ``defineToolkit``.
 *
 * Category files:
 * - ``./common``     — shared ``withSafeStatus`` wrapper + style constants
 * - ``./search``     — SearchKBUI, InternetSearchUI, QnaSearchUI
 * - ``./code``       — SearchCodebaseUI, RunTestsUI, ProposeFixUI, FetchUrlUI
 * - ``./pr-tools``   — CreatePrUI, FetchIssueUI
 * - ``./response``   — SendResponseUI, GradeResponseUI
 */

import { defineToolkit } from "@assistant-ui/react";
import { withSafeStatus } from "./common";
import { SearchKBUI, InternetSearchUI, QnaSearchUI } from "./search";
import { SearchCodebaseUI, RunTestsUI, ProposeFixUI, FetchUrlUI } from "./code";
import { CreatePrUI, FetchIssueUI } from "./pr-tools";
import { SendResponseUI, GradeResponseUI } from "./response";

export {
  withSafeStatus,
  SearchKBUI,
  InternetSearchUI,
  QnaSearchUI,
  SearchCodebaseUI,
  RunTestsUI,
  ProposeFixUI,
  FetchUrlUI,
  CreatePrUI,
  FetchIssueUI,
  SendResponseUI,
  GradeResponseUI,
};

export const ossiaToolkit = defineToolkit({
  search_knowledge_base: {
    type: "backend" as const,
    render: withSafeStatus(SearchKBUI),
  },
  internet_search: {
    type: "backend" as const,
    render: withSafeStatus(InternetSearchUI),
  },
  send_response: {
    type: "backend" as const,
    render: withSafeStatus(SendResponseUI),
  },
  grade_response: {
    type: "backend" as const,
    render: withSafeStatus(GradeResponseUI),
  },
  search_codebase: {
    type: "backend" as const,
    render: withSafeStatus(SearchCodebaseUI),
  },
  fetch_url: {
    type: "backend" as const,
    render: withSafeStatus(FetchUrlUI),
  },
  run_tests: {
    type: "backend" as const,
    render: withSafeStatus(RunTestsUI),
  },
  propose_fix: {
    type: "backend" as const,
    render: withSafeStatus(ProposeFixUI),
  },
  create_pr: {
    type: "backend" as const,
    render: withSafeStatus(CreatePrUI),
  },
  fetch_issue: {
    type: "backend" as const,
    render: withSafeStatus(FetchIssueUI),
  },
  qna_search: {
    type: "backend" as const,
    render: withSafeStatus(QnaSearchUI),
  },
});
