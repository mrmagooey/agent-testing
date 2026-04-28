/**
 * Fixture data for BenchmarkScorecardPanel tests.
 *
 * Covers:
 *  - A dataset with a mix of CWE rows (some with warnings, some without)
 *  - Rows where metrics are null (insufficient sample) vs fully populated
 *  - Aggregate row with a headline owasp_score
 *  - A second dataset to verify multi-dataset rendering
 */

import type { BenchmarkScorecard } from '../../api/client'

export const benchmarkScorecards: BenchmarkScorecard[] = [
  {
    dataset_name: 'BenchmarkPython-1.0',
    per_cwe: [
      {
        // Full metrics, no warning
        cwe_id: 'CWE-89',
        tp: 38,
        fp: 4,
        tn: 46,
        fn: 12,
        precision: 0.905,
        recall: 0.76,
        f1: 0.826,
        fp_rate: 0.08,
        owasp_score: 0.68,
        warning: null,
      },
      {
        // All metrics null — insufficient sample size warning
        cwe_id: 'CWE-22',
        tp: 3,
        fp: 1,
        tn: 4,
        fn: 2,
        precision: null,
        recall: null,
        f1: null,
        fp_rate: null,
        owasp_score: null,
        warning: 'insufficient sample size (n<25 per polarity)',
      },
      {
        // Partial metrics, different warning
        cwe_id: 'CWE-79',
        tp: 27,
        fp: 8,
        tn: 32,
        fn: 5,
        precision: 0.771,
        recall: 0.844,
        f1: 0.806,
        fp_rate: 0.2,
        owasp_score: 0.644,
        warning: 'high FP rate may indicate overfitting to positive examples',
      },
      {
        // Full metrics, no warning, negative owasp_score
        cwe_id: 'CWE-78',
        tp: 10,
        fp: 15,
        tn: 25,
        fn: 10,
        precision: 0.4,
        recall: 0.5,
        f1: 0.444,
        fp_rate: 0.375,
        owasp_score: 0.125,
        warning: null,
      },
    ],
    aggregate: {
      tp: 78,
      fp: 28,
      tn: 107,
      fn: 29,
      precision: 0.736,
      recall: 0.729,
      f1: 0.732,
      fp_rate: 0.207,
      owasp_score: 0.522,
      warning: null,
    },
  },
  {
    dataset_name: 'BenchmarkPython-2.0-beta',
    per_cwe: [
      {
        cwe_id: 'CWE-89',
        tp: 42,
        fp: 3,
        tn: 47,
        fn: 8,
        precision: 0.933,
        recall: 0.84,
        f1: 0.884,
        fp_rate: 0.06,
        owasp_score: 0.78,
        warning: null,
      },
    ],
    aggregate: {
      tp: 42,
      fp: 3,
      tn: 47,
      fn: 8,
      precision: 0.933,
      recall: 0.84,
      f1: 0.884,
      fp_rate: 0.06,
      owasp_score: 0.78,
      warning: null,
    },
  },
]

/** A single scorecard (used for focused component tests) */
export const singleScorecard: BenchmarkScorecard[] = [benchmarkScorecards[0]]
