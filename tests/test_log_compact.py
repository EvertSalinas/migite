#!/usr/bin/env python3
"""tests/test_log_compact.py — unittest coverage for migite.log_compact, the
rspec-aware log shrinker behind compact_rspec_log (lib/stack.sh). The shape it
replaces — a whole 795 KB mass-failure log in the heal prompt — is the
2026-09-28 'prompt too long' run in docs/improvements.md."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migite.log_compact import compact_rspec  # noqa: E402

LOG = """...................................................F..................

Failures:

  1) Product creating stores variants
     Failure/Error: expect(rows).to eq([['Rectangular', 18_000]])

       expected: [["Rectangular", 18000]]
            got: [["Redonda", 15000]]

       (compared using ==)
     # ./spec/models/product_spec.rb:61:in 'block (3 levels) in <main>'
     # ./spec/rails_helper.rb:20:in 'load'

  2) Product updating renames variants
     Failure/Error: expect(rows).to eq([['Rectangular', 18_000]])

       expected: [["Rectangular", 18000]]
            got: [["Redonda", 15000]]

       (compared using ==)
     # ./spec/models/product_spec.rb:144:in 'block (3 levels) in <main>'
     # ./spec/rails_helper.rb:20:in 'load'

  3) ProductVariant destroy cleans up
     Failure/Error: expect { v.destroy }.to change(described_class, :count).by(-1)
       expected `ProductVariant.count` to have changed by -1, but was changed by 1
     # ./spec/models/product_variant_spec.rb:240:in 'block (3 levels) in <main>'

Finished in 1.23 seconds (files took 0.5 seconds to load)
3 examples, 3 failures

Failed examples:

rspec ./spec/models/product_spec.rb:61 # Product creating stores variants
rspec ./spec/models/product_spec.rb:144 # Product updating renames variants
rspec ./spec/models/product_variant_spec.rb:240 # ProductVariant destroy cleans up
"""


def over_budget(text, budget=100):
    """Run compact_rspec with a budget the text exceeds."""
    assert len(text.encode()) > budget
    return compact_rspec(text, budget, "/logs/rspec.txt")


class CompactRspecTest(unittest.TestCase):
    def test_under_budget_passes_through_unchanged(self):
        self.assertEqual(compact_rspec(LOG, len(LOG.encode()), "/logs/rspec.txt"), LOG)

    def test_identical_failures_merge_with_a_count(self):
        out = over_budget(LOG, 2000)
        self.assertIn("3 total, 2 distinct", out)
        self.assertIn("[×2 failures with this exact error]", out)
        # The merged block keeps the first occurrence's frame; the rest are gone.
        self.assertIn("./spec/models/product_spec.rb:61", out)
        self.assertNotIn("rails_helper", out)

    def test_distinct_failures_all_survive(self):
        out = over_budget(LOG, 2000)
        self.assertIn("compared using ==", out)
        self.assertIn("ProductVariant.count", out)

    def test_summary_and_failed_examples_survive(self):
        out = over_budget(LOG, 2000)
        self.assertIn("3 examples, 3 failures", out)
        self.assertIn("rspec ./spec/models/product_variant_spec.rb:240", out)

    def test_progress_dots_are_dropped(self):
        out = over_budget(LOG, 2000)
        self.assertNotIn("......", out)

    def test_still_over_budget_elides_head_and_tail(self):
        big = LOG.replace("Finished in", "x" * 5000 + "\nFinished in")
        out = over_budget(big, 2000)
        self.assertLessEqual(len(out.encode()), 2200)   # budget + marker overhead
        self.assertIn("bytes elided — full log: /logs/rspec.txt", out)

    def test_no_failures_section_falls_back_to_byte_cap(self):
        text = "line\n" * 5000
        out = over_budget(text, 2000)
        self.assertLessEqual(len(out.encode()), 2200)
        self.assertIn("bytes elided", out)


if __name__ == "__main__":
    unittest.main()
