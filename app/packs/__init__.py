"""
Industry packs.

One Regulars instance serves one business in one industry, chosen at start-up
with the REGULARS_INDUSTRY environment variable (default: restaurant). The
engine is the same for every industry; a pack supplies what a customer's
behaviour means there.

A pack is a module that provides:

    NAME, LABEL             id and human name ("restaurant", "Cafes, ...")
    VENUE                   demo business identity (name, suburb, abn)
    DEFAULT_GOALS           goals, offer catalogue and trigger->offer pairing
    demo_dataset()          a synthetic business to explore before importing
    sample_simple(), sample_square()   example upload files
    DEFAULT_THRESHOLDS      every number the triggers use, editable by the owner
    THRESHOLD_FIELDS        (key, label, help, step) for the tuning screen
    OFFER_CATEGORIES        (id, label, help) offer groups
    TRIGGERS                ordered rules: id, name, rule, offer_cat, serves,
                            weight, fn(profile, goals, thresholds) -> (ok, evidence)
    prepare(dataset)        called before each run
    venue_stats(dataset)    business-wide facts the copy can use
    profile(customer, now, spend_pct_fn)
                            what this customer does; must include
                            empty, visits and recency_days
    copy(trigger_id, evidence, customer, profile, goals, stats, now, offer,
         offer_note)        -> (subject, body, why); customer copy never
                            states a number about the customer
    explain_no_match(profile, goals)

CSV column matching still lives in importer.py; it moves into the pack when
a second industry needs different columns.
"""

import importlib
import os

AVAILABLE = ("restaurant",)


def load(name=None):
    name = (name or os.environ.get("REGULARS_INDUSTRY") or "restaurant").strip().lower()
    if name not in AVAILABLE:
        raise SystemExit(f"Unknown REGULARS_INDUSTRY {name!r}. "
                         f"Available: {', '.join(AVAILABLE)}.")
    return importlib.import_module(f"packs.{name}")
