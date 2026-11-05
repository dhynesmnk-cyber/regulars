"""
Tests for Regulars. Run from the repo root:

    python3 -m unittest discover -s tests -t . -v

Covers: dataset integrity, one-message-per-person, offer/behaviour matching,
the no-surveillance copy rule, suppression, Spam Act footer, goals/threshold/
catalogue sensitivity, CSV import, persistence, and the HTTP routes end to end.
"""

import csv as _csv
import html as _h
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse, request
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# keep test audit writes out of the app directory
_AUDIT_TMP = tempfile.mkdtemp(prefix="regulars-tests-")
os.environ.setdefault("REGULARS_AUDIT_LOG", os.path.join(_AUDIT_TMP, "audit.log"))

import data as D           # noqa: E402
import engine as E         # noqa: E402
import importer as IMP     # noqa: E402
import store               # noqa: E402
import oauth as OAUTH      # noqa: E402
import security as SEC     # noqa: E402
import server as S         # noqa: E402
import state as ST         # noqa: E402


class _NoRedirect(request.HTTPRedirectHandler):
    """POSTs answer 303; we assert on the redirect rather than follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


DS = D.build()
ALL_ORDERS = [o for c in DS["customers"] for o in c["orders"]]
R = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS)


def _goals(**kw):
    g = dict(D.DEFAULT_GOALS)
    g.update(kw)
    return g


# ---------------------------------------------------------------------------
class TestDataset(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(len(DS["customers"]), 525)
        self.assertEqual(DS["stats"]["orders"], len(ALL_ORDERS))
        self.assertGreater(DS["stats"]["orders"], 7000)
        self.assertGreater(DS["stats"]["revenue"], 100_000)

    def test_deterministic(self):
        a, b = D.build(), D.build()
        self.assertEqual(a["stats"], b["stats"])
        self.assertEqual([len(c["orders"]) for c in a["customers"]],
                         [len(c["orders"]) for c in b["customers"]])

    def test_every_order_is_well_formed(self):
        names = {m["name"] for m in DS["menu"]}
        seen = set()
        for c in DS["customers"]:
            for o in c["orders"]:
                self.assertNotIn(o["id"], seen)
                seen.add(o["id"])
                self.assertTrue(o["items"])
                for it in o["items"]:
                    self.assertIn(it["name"], names)
                    self.assertGreater(it["qty"], 0)
                self.assertGreater(o["net"], 0)
                self.assertIn(o["daypart"], ("morning", "midday", "evening"))
                ts = datetime.fromisoformat(o["at"])
                self.assertEqual(ts.weekday(), o["weekday"])
                self.assertLessEqual(ts, datetime.fromisoformat(DS["now"]))
            ats = [o["at"] for o in c["orders"]]
            self.assertEqual(ats, sorted(ats))

    def test_archetypes_are_represented(self):
        seen = Counter(c["archetype"] for c in DS["customers"])
        for a in D.ARCHETYPES:
            self.assertEqual(seen[a[0]], a[1])

    def test_birthday_pass_does_not_perturb_orders(self):
        import importlib
        saved = D.BIRTHDAY_CAPTURE_RATE
        try:
            D.BIRTHDAY_CAPTURE_RATE = 1.0
            importlib.reload(D)
            D.BIRTHDAY_CAPTURE_RATE = 1.0
            heavy = D.build()
            self.assertEqual([len(c["orders"]) for c in heavy["customers"]],
                             [len(c["orders"]) for c in DS["customers"]])
            self.assertGreater(sum(1 for c in heavy["customers"] if c["birthday"]),
                               sum(1 for c in DS["customers"] if c["birthday"]))
        finally:
            importlib.reload(D)
            self.assertEqual(D.BIRTHDAY_CAPTURE_RATE, saved)

    def test_dataset_contains_fading_customers(self):
        trends = [p["gap_trend"] for p in R["profiles"].values() if p.get("gap_trend")]
        self.assertGreater(len(trends), 200)
        self.assertGreater(sum(1 for t in trends if t >= E.DEFAULT_THRESHOLDS["drift_trend_min"]), 20)

    def test_default_goals_reference_real_menu_items(self):
        names = {m["name"] for m in DS["menu"]}
        for p in D.DEFAULT_GOALS["push_products"]:
            self.assertIn(p, names)

    def test_catalogue_is_sane(self):
        g = D.DEFAULT_GOALS
        cats = {k for k, _, _ in E.OFFER_CATEGORIES}
        for o in g["catalog"]:
            self.assertIn(o["cat"], cats)
            self.assertTrue(o["label"].strip())
            self.assertGreaterEqual(o["cost"], 0)
        for t in E.TRIGGERS:
            self.assertIn(t["id"], g["trigger_offer"], f"{t['id']} has no default offer")
            self.assertIn(g["trigger_offer"][t["id"]], {o["id"] for o in g["catalog"]})


# ---------------------------------------------------------------------------
class TestEngine(unittest.TestCase):
    def test_every_trigger_fires(self):
        dead = [t["id"] for t in E.TRIGGERS if R["trigger_counts"].get(t["id"], 0) == 0]
        self.assertEqual(dead, [], f"triggers that never match: {dead}")

    def test_exactly_one_message_per_customer(self):
        ids = [m["customer_id"] for m in R["messages"]]
        dupes = [k for k, v in Counter(ids).items() if v > 1]
        self.assertEqual(dupes, [])

    def test_counts_reconcile(self):
        s = R["summary"]
        self.assertEqual(s["ready"] + s["suppressed"] + s["no_match"], s["customers"])

    def test_message_shape(self):
        for m in R["messages"]:
            for k in ("id", "customer_id", "subject", "body", "why", "evidence",
                      "offer_id", "offer_cat", "offer_label", "offer_cost",
                      "trigger_id", "profile"):
                self.assertIn(k, m)
            self.assertLess(len(m["subject"]), 90)
            self.assertTrue(m["evidence"])
            self.assertGreaterEqual(m["offer_cost"], 0)

    def test_offer_cost_is_bounded(self):
        self.assertLess(R["summary"]["total_offer_cost_if_all_redeem"], 15_000)


# ---------------------------------------------------------------------------
class TestCopyTone(unittest.TestCase):
    """The standing rule: customer-facing copy may name their usual and use
    vague time. It may never quote their pattern back at them."""

    PATTERN = None

    def setUp(self):
        import re
        self.PATTERN = re.compile(r"\d+\s*(days?|weeks?|months?|times|visits|percent)", re.I)

    def test_no_numbers_about_their_behaviour(self):
        for m in R["messages"]:
            self.assertIsNone(self.PATTERN.search(m["body"]),
                              f"behaviour number in email to {m['customer']['name']}: {m['subject']}")
            self.assertIsNone(self.PATTERN.search(m["subject"]))
            self.assertNotIn("we noticed", m["body"].lower())
            self.assertNotIn("percentile", m["body"].lower())
            self.assertNotIn("every ", m["body"].lower().split("unsubscribe")[0]
                             .replace("every one", "").replace("every good table", "")
                             .replace("every week", ""))

    def test_every_subject_is_personal(self):
        for m in R["messages"]:
            self.assertIn(m["customer"]["first_name"], m["subject"])

    def test_subjects_are_mostly_distinct(self):
        s = R["summary"]
        self.assertGreaterEqual(s["unique_subjects"] / s["ready"], 0.75,
                                "subjects collapsing into a template blast")

    def test_the_numbers_still_exist_for_the_owner(self):
        """Vague copy must not mean vague analysis: the evidence panel keeps
        the figures, they just never leave the app."""
        with_num = sum(1 for m in R["messages"] if m["evidence"])
        self.assertEqual(with_num, R["summary"]["ready"])
        sig = [m for m in R["messages"] if m["trigger_id"] == "signature_overdue"][0]
        self.assertIn("since", sig["evidence"])
        self.assertIn("multiple", sig["evidence"])
        self.assertIn("<b>", sig["why"], "owner-facing why should carry the figures")

    def test_vague_time_is_monotonic_and_sane(self):
        self.assertEqual(E._vague(5), "a couple of weeks")
        self.assertEqual(E._vague(30), "a few weeks")
        self.assertEqual(E._vague(60), "a month or two")
        self.assertEqual(E._vague(120), "a season")
        self.assertEqual(E._vague(400), "far too long")

    def test_no_blanket_discount_language(self):
        banned = ["for everyone", "all our customers", "sitewide", "valued customer",
                  "dear customer"]
        for m in R["messages"]:
            low = m["body"].lower()
            for b in banned:
                self.assertNotIn(b, low)


# ---------------------------------------------------------------------------
class TestOfferFitsThePerson(unittest.TestCase):
    def test_signature_overdue_names_their_real_top_item(self):
        for m in R["messages"]:
            if m["trigger_id"] != "signature_overdue":
                continue
            self.assertEqual(m["evidence"]["item"], m["profile"]["signature_item"])
            self.assertIn(m["profile"]["signature_item"].lower(), m["body"].lower())

    def test_habit_breaker_only_offers_something_never_bought(self):
        n = 0
        for m in R["messages"]:
            if m["trigger_id"] != "habit_breaker":
                continue
            n += 1
            item = m["evidence"]["new_item"]
            self.assertNotIn(item, m["profile"]["ever_bought"])
            self.assertIn(item.lower(), m["body"].lower())
        self.assertGreater(n, 0)

    def test_never_adds_extras_only_goes_to_low_attach_customers(self):
        for m in R["messages"]:
            if m["trigger_id"] != "never_adds_extras":
                continue
            self.assertLessEqual(m["profile"]["attach_rate"],
                                 E.DEFAULT_THRESHOLDS["extras_max_attach"] + 1e-9)
            self.assertGreaterEqual(m["profile"]["visits"], 4)

    def test_winback_is_only_for_real_lapses(self):
        for m in R["messages"]:
            if m["trigger_id"] != "lapsed_big_spender":
                continue
            self.assertGreaterEqual(m["profile"]["recency_days"],
                                    E.DEFAULT_THRESHOLDS["lapsed_days"])

    def test_upsells_never_go_to_lapsed_customers(self):
        for m in R["messages"]:
            if m["trigger_id"] in ("never_adds_extras", "habit_breaker"):
                self.assertLess(m["profile"]["recency_days"],
                                E.DEFAULT_THRESHOLDS["lapsed_days"])

    def test_bring_a_friend_goes_to_social_customers(self):
        for m in R["messages"]:
            if m["trigger_id"] != "social_hub":
                continue
            self.assertGreaterEqual(m["profile"]["avg_party"],
                                    E.DEFAULT_THRESHOLDS["social_avg_party"])
            self.assertGreaterEqual(m["profile"]["visits"], 4)

    def test_big_table_is_for_occasional_groups_only(self):
        for m in R["messages"]:
            if m["trigger_id"] != "big_table":
                continue
            self.assertGreaterEqual(m["profile"]["max_party"],
                                    E.DEFAULT_THRESHOLDS["big_max_party"])
            self.assertLess(m["profile"]["avg_party"],
                            E.DEFAULT_THRESHOLDS["big_avg_party_max"])

    def test_birthday_is_time_bound(self):
        for m in R["messages"]:
            if m["trigger_id"] != "birthday":
                continue
            d = m["profile"]["birthday_days"]
            self.assertIsNotNone(d)
            self.assertLessEqual(d, E.DEFAULT_THRESHOLDS["birthday_window_days"])
            self.assertGreaterEqual(d, 0)

    def test_wrong_time_is_plausible_for_the_recipient(self):
        for m in R["messages"]:
            if m["trigger_id"] != "wrong_time":
                continue
            p = m["profile"]
            if m["evidence"]["target_daypart"] == "evening":
                self.assertTrue(p["daypart_counts"].get("evening", 0) > 0
                                or p["alcohol_share"] > 0)
            # the invite must not reveal when we think they usually come
            self.assertNotIn("we mostly see you", m["body"].lower())

    def test_drifting_is_a_real_trend_on_a_still_active_customer(self):
        th = E.DEFAULT_THRESHOLDS
        for m in R["messages"]:
            if m["trigger_id"] != "drifting":
                continue
            p = m["profile"]
            self.assertGreaterEqual(p["visits"], th["drift_min_visits"])
            self.assertGreaterEqual(p["gap_trend"], th["drift_trend_min"])
            self.assertLessEqual(p["recency_days"], th["drift_max_recency"])
            self.assertTrue(not p["overdue"] or p["overdue"] < th["upsell_overdue_max"])
            import re as _re
            self.assertIsNone(_re.search(r"\d+\s*days", m["body"]),
                              "drift email quotes a day count")
            self.assertIsNone(_re.search(r"every\s+\d+", m["body"]),
                              "drift email quotes a rhythm")


# ---------------------------------------------------------------------------
class TestSuppression(unittest.TestCase):
    def test_unsubscribed_never_receive_anything(self):
        unsub = {c["id"] for c in DS["customers"] if c["unsubscribed"]}
        self.assertTrue(unsub)
        self.assertEqual(unsub & {m["customer_id"] for m in R["messages"]}, set())

    def test_no_email_never_receive_anything(self):
        noemail = {c["id"] for c in DS["customers"] if not c["email"]}
        self.assertTrue(noemail)
        self.assertEqual(noemail & {m["customer_id"] for m in R["messages"]}, set())

    def test_consent_required(self):
        noconsent = {c["id"] for c in DS["customers"] if not c["email_consent"]}
        self.assertTrue(noconsent)
        self.assertEqual(noconsent & {m["customer_id"] for m in R["messages"]}, set())

    def test_recent_visitors_are_left_alone(self):
        skip = D.DEFAULT_GOALS["skip_if_visited_within_days"]
        for m in R["messages"]:
            self.assertGreater(m["profile"]["recency_days"], skip)

    def test_frequency_cap_is_enforced(self):
        target = next(c for c in DS["customers"]
                      if c["id"] in {m["customer_id"] for m in R["messages"]})
        recent = (datetime.fromisoformat(DS["now"]) - timedelta(days=2)).isoformat()
        r2 = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS, {target["id"]: recent})
        self.assertNotIn(target["id"], {m["customer_id"] for m in r2["messages"]})
        self.assertEqual(r2["summary"]["ready"], R["summary"]["ready"] - 1)

    def test_no_match_is_reported_not_hidden(self):
        self.assertGreater(R["summary"]["no_match"], 0)


# ---------------------------------------------------------------------------
class TestSpamActCompliance(unittest.TestCase):
    def test_footer_on_every_message(self):
        for m in R["messages"]:
            b = m["body"]
            self.assertIn(D.VENUE["name"], b)
            self.assertIn(f'ABN {D.VENUE["abn"]}', b)
            self.assertIn(D.DEFAULT_GOALS["unsub_tag"], b)
            self.assertIn("You're getting this because", b)
            self.assertIn("reply", b.lower())

    def test_no_html_or_tracking_in_the_copy(self):
        for m in R["messages"]:
            for bad in ("<img", "<a href", "open=", "click here"):
                self.assertNotIn(bad, m["body"].lower())

    def test_export_carries_consent_basis(self):
        header = [ln for ln in S.export_csv().splitlines() if ln.startswith("Email,")][0]
        for col in ("Email", "First Name", "Subject", "Body", "Offer", "Offer Category",
                    "Trigger", "Consent Source", "Consent Date", "Holdout"):
            self.assertIn(col, header)

    def test_export_holds_back_a_control_group(self):
        rows = list(_csv.reader(io.StringIO(S.export_csv())))
        hdr = rows.index(["Email", "First Name", "Last Name", "Subject", "Body", "Offer",
                          "Offer Category", "Trigger", "Consent Source", "Consent Date",
                          "Holdout"])
        body = [r for r in rows[hdr + 1:] if r]
        held = [r for r in body if r[-1] == "yes"]
        self.assertTrue(held)
        self.assertAlmostEqual(len(held) / len(body), 0.10, delta=0.03)

    def test_export_never_leaks_spend_or_phone(self):
        csv_text = S.export_csv()
        header = [ln for ln in csv_text.splitlines() if ln.startswith("Email,")][0]
        for col in ("Phone", "Spend", "Lifetime", "Card", "Order", "Items"):
            self.assertNotIn(col, header)
        for ph in {c["phone"] for c in DS["customers"] if c.get("phone")}:
            self.assertNotIn(ph, csv_text)


# ---------------------------------------------------------------------------
class TestGoalsThresholdsCatalogue(unittest.TestCase):
    def test_removing_push_times_removes_wrong_time(self):
        g = _goals(push_times=[])
        r = E.run(DS, g, g)
        self.assertEqual(r["trigger_counts"].get("wrong_time", 0), 0)
        self.assertGreater(R["trigger_counts"]["wrong_time"], 0)

    def test_push_products_reach_the_copy(self):
        g = _goals(push_products=["Chocolate Brownie"])
        r = E.run(DS, g, g)
        bodies = " ".join(m["body"] for m in r["messages"]
                          if m["trigger_id"] == "habit_breaker").lower()
        self.assertIn("chocolate brownie", bodies)

    def test_disabling_a_trigger_removes_it_entirely(self):
        enabled = {t["id"] for t in E.TRIGGERS} - {"birthday"}
        r = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS, enabled_triggers=enabled)
        self.assertEqual(r["trigger_counts"].get("birthday", 0), 0)
        ids = [m["customer_id"] for m in r["messages"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_priority_is_a_nudge_not_an_override(self):
        order = [t["id"] for t in E._ordered_triggers(_goals(priority="repeat"))]
        self.assertLess(order.index("signature_overdue"), order.index("drifting"))
        self.assertLess(order.index("social_hub"), order.index("drifting"))
        self.assertEqual(order[0], "birthday")

    def test_priority_changes_the_mix(self):
        bb = E.run(DS, _goals(priority="bigger_basket"), _goals(priority="bigger_basket"))
        hm = E.run(DS, _goals(priority="high_margin"), _goals(priority="high_margin"))
        self.assertGreater(bb["trigger_counts"]["never_adds_extras"],
                           R["trigger_counts"]["never_adds_extras"])
        self.assertGreater(hm["trigger_counts"]["habit_breaker"],
                           R["trigger_counts"]["habit_breaker"])

    def test_every_trigger_is_reachable_under_some_priority(self):
        seen = Counter()
        for prio in ("repeat", "bigger_basket", "fill_quiet", "new_customers", "high_margin"):
            g = _goals(priority=prio)
            for tid, n in E.run(DS, g, g)["trigger_counts"].items():
                seen[tid] += n
        self.assertEqual([t["id"] for t in E.TRIGGERS if seen[t["id"]] == 0], [])

    def test_thresholds_change_the_output(self):
        r = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS,
                  thresholds={"sig_overdue_mult": 3.0})
        self.assertEqual(r["trigger_counts"]["signature_overdue"], 0)
        r2 = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS,
                   thresholds={"lapsed_days": 400})
        self.assertEqual(r2["trigger_counts"]["lapsed_big_spender"], 0)
        r3 = E.run(DS, D.DEFAULT_GOALS, D.DEFAULT_GOALS,
                   thresholds={"birthday_window_days": 120})
        self.assertGreater(r3["trigger_counts"]["birthday"],
                           R["trigger_counts"]["birthday"])

    def test_threshold_defaults_are_all_exposed_in_the_ui_metadata(self):
        keys = {k for k, _, _, _ in E.THRESHOLD_FIELDS}
        self.assertEqual(keys, set(E.DEFAULT_THRESHOLDS),
                         "every threshold must be editable, none hidden")

    def test_swapping_a_triggers_offer_reaches_the_copy(self):
        g = _goals(trigger_offer={**D.DEFAULT_GOALS["trigger_offer"],
                                  "signature_overdue": "off_15"})
        r = E.run(DS, g, g)
        m = [x for x in r["messages"] if x["trigger_id"] == "signature_overdue"][0]
        self.assertEqual(m["offer_label"], "$15 off your bill")
        self.assertIn("$15 off your bill", m["body"])
        self.assertEqual(m["offer_cat"], "money_off")

    def test_custom_catalogue_offer_can_be_used(self):
        cat = D.DEFAULT_GOALS["catalog"] + [
            {"id": "own_test", "cat": "upgrade", "label": "a sausage for your dog", "cost": 1.0}]
        g = _goals(catalog=cat,
                   trigger_offer={**D.DEFAULT_GOALS["trigger_offer"],
                                  "social_hub": "own_test"})
        r = E.run(DS, g, g)
        m = [x for x in r["messages"] if x["trigger_id"] == "social_hub"][0]
        self.assertIn("sausage for your dog", m["body"])


# ---------------------------------------------------------------------------
class TestImport(unittest.TestCase):
    NOW = datetime(2026, 11, 4, 9, 0)

    def test_simple_format_round_trip(self):
        text = D.sample_simple(200)
        visits, people, rep = IMP.parse_simple(text, [])
        self.assertGreater(len(visits), 50)
        self.assertFalse(rep.get("error"))
        menu = IMP.build_menu(visits)
        self.assertGreater(len(menu), 3)
        ds = IMP.to_dataset(visits, people, menu, self.NOW, attested_consent=True)
        self.assertGreater(ds["stats"]["customers"], 5)
        self.assertTrue(all(c["email_consent"] for c in ds["customers"]))
        r = E.run(ds, D.DEFAULT_GOALS, D.DEFAULT_GOALS)
        self.assertGreater(r["summary"]["ready"], 0)

    def test_simple_format_rejects_missing_columns(self):
        visits, people, rep = IMP.parse_simple("a,b\n1,2\n", [])
        self.assertIn("error", rep)
        self.assertEqual(visits, [])

    def test_square_format_round_trip(self):
        tx, cu = D.sample_square(30)
        visits, people, rep = IMP.parse_square(tx, cu, [])
        self.assertEqual(rep["fields_missing"], [])
        self.assertIn("covers", rep["fields_found"])
        menu = IMP.build_menu(visits)
        priced = [m for m in menu if m["price"] > 0]
        self.assertTrue(priced, "square export carries prices; we should use them")
        ds = IMP.to_dataset(visits, people, menu, self.NOW, attested_consent=True)
        self.assertGreater(ds["stats"]["revenue"], 0)
        r = E.run(ds, D.DEFAULT_GOALS, D.DEFAULT_GOALS)
        self.assertGreater(r["summary"]["ready"], 0)

    def test_square_format_without_consent_quarantines_everyone(self):
        tx, cu = D.sample_square(20)
        visits, people, rep = IMP.parse_square(tx, cu, [])
        menu = IMP.build_menu(visits)
        ds = IMP.to_dataset(visits, people, menu, self.NOW, attested_consent=False)
        # sample directory marks opt-in per customer; those without are quarantined
        r = E.run(ds, D.DEFAULT_GOALS, D.DEFAULT_GOALS)
        consented = sum(1 for c in ds["customers"] if c["email_consent"])
        self.assertEqual(r["summary"]["suppressed"] + r["summary"]["no_match"]
                         + r["summary"]["ready"], ds["stats"]["customers"])
        if consented == 0:
            self.assertEqual(r["summary"]["ready"], 0)

    def test_category_guessing(self):
        self.assertEqual(IMP.guess_category("Garlic Bread"), "Sides")
        self.assertEqual(IMP.guess_category("Chocolate Brownie"), "Dessert")
        self.assertEqual(IMP.guess_category("Espresso Martini"), "Bar")
        self.assertEqual(IMP.guess_category("Lamb Shank"), "Mains")


# ---------------------------------------------------------------------------
class TestStore(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            old = store.PATH
            store.PATH = path
            try:
                snap = {"connected": True, "thresholds": {"lapsed_days": 90},
                        "sender": {"tool": "mailchimp"}, "sent": [], "sent_history": {},
                        "approved": ["a", "b"],
                        "goals": D.DEFAULT_GOALS, "enabled": ["birthday"],
                        "source": "demo"}
                store.save(snap)
                back = store.load()
                self.assertTrue(back["connected"])
                self.assertEqual(back["thresholds"]["lapsed_days"], 90)
                self.assertEqual(back["sender"]["tool"], "mailchimp")
                self.assertEqual(sorted(back["approved"]), ["a", "b"])
            finally:
                store.PATH = old

    def test_missing_file_is_fine(self):
        old = store.PATH
        store.PATH = "/nonexistent/dir/state.json"
        try:
            self.assertEqual(store.load(), {})
        finally:
            store.PATH = old


# ---------------------------------------------------------------------------
class _HTTPHarness:
    """A live server on a random port with a throwaway state file, plus
    get/post helpers. Mixed into the HTTP test classes."""

    @classmethod
    def setUpClass(cls):
        # tests must never touch the operator's real state file
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.PATH
        store.PATH = os.path.join(cls._tmp.name, "state.json")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        store.PATH = cls._old_path
        cls._tmp.cleanup()

    def setUp(self):
        S.STATE.update(S.fresh_state())
        S.STATE["connected"] = True
        S.persist()

    def tearDown(self):
        S.STATE.update(S.fresh_state())
        S.persist()

    def get(self, path):
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(self.base + path, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def post(self, path, **form):
        body = parse.urlencode(form, doseq=True).encode()
        req = request.Request(self.base + path, data=body,
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def post_multipart(self, path, fields, files):
        boundary = "----regularstestboundary"
        buf = io.BytesIO()
        for k, v in fields.items():
            buf.write(f"--{boundary}\r\nContent-Disposition: form-data; "
                      f'name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fname, content) in files.items():
            buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                      f'filename="{fname}"\r\nContent-Type: text/csv\r\n\r\n'.encode())
            buf.write(content.encode() if isinstance(content, str) else content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        raw = buf.getvalue()
        req = request.Request(self.base + path, data=raw,
                              headers={"Content-Type":
                                       f"multipart/form-data; boundary={boundary}"})
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers


class TestHTTP(_HTTPHarness, unittest.TestCase):
    # -- pages -------------------------------------------------------------
    def test_every_page_renders(self):
        for path in ("/", "/goals", "/triggers", "/inbox", "/sending", "/sent", "/data",
                     "/security", "/login"):
            st, body, _ = self.get(path)
            self.assertEqual(st, 200, path)
            self.assertTrue(body.endswith("</html>"), path)
            self.assertTrue("Traceback" not in body, path)
            for ph in ("{{title}}", "{{body}}", "{{nav}}", "{{css}}", "{{venue}}"):
                self.assertTrue(ph not in body, f"unrendered {ph} on {path}")
        st, body, _ = self.get("/healthz")
        self.assertEqual(st, 200)
        hz = json.loads(body)
        self.assertEqual(hz["status"], "ok")
        self.assertTrue(hz["version"])

    def test_unconnected_setup_offers_both_paths(self):
        S.STATE["connected"] = False
        _, body, _ = self.get("/")
        self.assertTrue("Connect Square" in body)
        self.assertTrue("upload" in body.lower())
        self.assertTrue("/sample/simple.csv" in body)

    def test_merge_tag_on_pages_that_show_copy(self):
        for path in ("/inbox", f"/m?id={R['messages'][0]['id']}"):
            _, body, _ = self.get(path)
            self.assertTrue(D.DEFAULT_GOALS["unsub_tag"] in body, path)

    def test_unknown_route_is_404(self):
        st, _, _ = self.get("/nope")
        self.assertEqual(st, 404)

    def test_message_detail_page(self):
        mid = R["messages"][0]["id"]
        _, body, _ = self.get(f"/m?id={mid}")
        self.assertTrue("Why this offer" in body)
        self.assertTrue("yours to see, never theirs" in body)

    # -- flows -------------------------------------------------------------
    def test_connect_flow(self):
        S.STATE["connected"] = False
        st, _, hdr = self.post("/connect")
        self.assertEqual((st, hdr["Location"]), (303, "/goals"))
        self.assertTrue(S.STATE["connected"])

    def test_upload_simple_csv_with_consent(self):
        S.STATE["connected"] = False
        st, _, hdr = self.post_multipart("/upload", {"consent": "1"},
                                         {"f_main": ("simple.csv", D.sample_simple(300))})
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        self.assertEqual(S.STATE["source"], "csv")
        self.assertTrue(S.STATE["connected"])
        rep = S.STATE["import_report"]
        self.assertGreater(rep["customers"], 3)
        self.assertEqual(rep["mode"], "simple")
        self.assertGreater(S.result()["summary"]["ready"], 0)
        _, body, _ = self.get("/")
        self.assertTrue("Import report" in body)

    def test_upload_without_consent_quarantines(self):
        tx, _ = D.sample_square(15)
        # strip the opt-in column so nobody has consent
        rows = [r for r in _csv.reader(io.StringIO(tx))]
        hdr = rows[0]
        if "Marketing opt-in" in hdr:
            i = hdr.index("Marketing opt-in")
            rows = [r[:i] + r[i + 1:] for r in rows]
        cleaned = "\n".join(",".join(r) for r in rows)
        self.post_multipart("/upload", {}, {"f_main": ("t.csv", cleaned)})
        self.assertEqual(S.result()["summary"]["ready"], 0)
        self.assertIn("no_consent", S.result()["summary"]["suppression_breakdown"])

    def test_upload_square_pair(self):
        tx, cu = D.sample_square(25)
        self.post_multipart("/upload", {"consent": "1"},
                            {"f_main": ("tx.csv", tx), "f_cust": ("cu.csv", cu)})
        self.assertEqual(S.STATE["source"], "csv")
        self.assertEqual(S.STATE["import_report"]["fields_missing"], [])
        self.assertGreater(S.result()["summary"]["ready"], 0)

    def test_upload_garbage_reports_an_error(self):
        self.post_multipart("/upload", {}, {"f_main": ("bad.csv", "x,y\n1,2\n")})
        self.assertFalse(S.STATE["connected"])
        self.assertIn("error", S.STATE["import_report"])

    def test_samples_download(self):
        for path in ("/sample/simple.csv", "/sample/square-transactions.csv",
                     "/sample/square-customers.csv"):
            st, body, hdr = self.get(path)
            self.assertEqual(st, 200, path)
            self.assertIn("text/csv", hdr["Content-Type"])
            self.assertGreater(len(body.splitlines()), 5)

    def test_goals_post_updates_catalogue_and_pairings(self):
        st, _, hdr = self.post("/goals",
                               slot=["evening|1"], product=["Chocolate Brownie"],
                               priority="bigger_basket",
                               id_0="upg_oat", cat_0="upgrade", label_0="an oat milk upgrade on us",
                               cost_0="0.6",
                               id_1="free_side", cat_1="free_item", label_1="a free side",
                               cost_1="3.1",
                               new_cat="group", new_label="a free tiramisu", new_cost="6.5",
                               toffer_signature_overdue="upg_oat",
                               toffer_social_hub="free_side",
                               min_days="21", skip_days="3")
        self.assertEqual(hdr["Location"], "/triggers")
        g = S.STATE["goals"]
        self.assertEqual(g["priority"], "bigger_basket")
        self.assertEqual(g["min_days_between_sends"], 21)
        self.assertIn("a free tiramisu", [o["label"] for o in g["catalog"]])
        self.assertEqual(g["trigger_offer"]["social_hub"], "free_side")
        self.assertEqual(len(g["catalog"]), len(D.DEFAULT_GOALS["catalog"]) + 1,
                         "rows not present in the form are left untouched")
        # dropping is explicit, via the checkbox
        form = {"drop_0": "1"}
        for i, o in enumerate(S.STATE["goals"]["catalog"][:3]):
            form.update({f"id_{i}": o["id"], f"cat_{i}": o["cat"],
                         f"label_{i}": o["label"], f"cost_{i}": o["cost"]})
        self.post("/goals", **form)
        self.assertEqual(len(S.STATE["goals"]["catalog"]),
                         len(D.DEFAULT_GOALS["catalog"]))

    def test_tuning_post_and_reset(self):
        self.post("/triggers", t=["birthday", "signature_overdue"],
                  th_lapsed_days="200", th_sig_overdue_mult="2")
        self.assertEqual(S.STATE["enabled"], {"birthday", "signature_overdue"})
        self.assertEqual(S.STATE["thresholds"]["lapsed_days"], 200)
        self.assertEqual(S.STATE["thresholds"]["sig_overdue_mult"], 2)
        self.assertEqual(S.result()["trigger_counts"]["lapsed_big_spender"], 0)
        self.post("/tuning/reset")
        self.assertEqual(S.STATE["thresholds"], {})

    def test_tuning_rejects_nonsense(self):
        self.post("/triggers", th_lapsed_days="-50", th_birthday_window_days="99999")
        self.assertEqual(S.STATE["thresholds"]["lapsed_days"], 0)
        self.assertLessEqual(S.STATE["thresholds"]["birthday_window_days"], 1000)

    def test_sending_post_rewrites_the_merge_tag(self):
        self.post("/sending", tool="mailchimp", smtp_host="", smtp_port="587")
        self.assertEqual(S.STATE["sender"]["tool"], "mailchimp")
        self.assertEqual(S.STATE["goals"]["unsub_tag"], "*|UNSUB|*")
        _, body, _ = self.get("/export-copy.txt")
        self.assertIn("*|UNSUB|*", S.export_csv())
        self.post("/sending", tool="klaviyo")
        self.assertIn("{% unsubscribe %}", S.export_csv())
        self.post("/sending", tool="none")
        self.assertEqual(S.STATE["goals"]["unsub_tag"], "{{unsubscribe_url}}")

    def test_sending_page_links_the_common_tools(self):
        _, body, _ = self.get("/sending")
        for url in ("https://mailchimp.com", "https://www.klaviyo.com",
                    "https://www.brevo.com", "https://www.mailerlite.com"):
            self.assertIn(url, body)

    def test_smtp_test_records_a_result_without_crashing(self):
        self.post("/sending/test", tool="smtp", smtp_host="localhost", smtp_port="9",
                  smtp_user="", smtp_pass="", from_addr="", reply_to="")
        self.assertIn("test_result", S.STATE["sender"])
        self.assertFalse(S.STATE["sender"]["test_result"]["ok"])
        _, body, _ = self.get("/sending")
        self.assertTrue("Connection test" in body)

    def test_approve_adds_to_ledger_and_caps_that_person(self):
        m = R["messages"][0]
        st, _, hdr = self.post(f"/approve?id={m['id']}")
        self.assertEqual(hdr["Location"], "/inbox")
        self.assertEqual(len(S.STATE["sent"]), 1)
        rec = S.STATE["sent"][0]
        self.assertEqual(rec["customer_id"], m["customer_id"])
        self.assertIn("consent_basis", rec)
        self.assertNotIn(m["customer_id"],
                         {x["customer_id"] for x in S.result()["messages"]})

    def test_approve_with_edit_keeps_the_edit(self):
        m = R["messages"][0]
        self.post("/approve-edit", id=m["id"], subject="Mine", body="Mine too")
        self.assertEqual(S.STATE["sent"][0]["subject"], "Mine")

    def test_sent_page_lists_the_ledger(self):
        m = R["messages"][0]
        self.post(f"/approve?id={m['id']}")
        _, body, _ = self.get("/sent")
        self.assertTrue(_h.escape(m["customer"]["name"]) in body)
        self.assertTrue("append-only" in body)

    def test_csv_export(self):
        st, body, hdr = self.get("/export.csv")
        self.assertEqual(st, 200)
        self.assertIn("text/csv", hdr["Content-Type"])
        rows = [r for r in list(_csv.reader(io.StringIO(body))) if r and not r[0].startswith("#")]
        self.assertEqual(rows[0][0], "Email")
        self.assertEqual(len(rows), R["summary"]["ready"] + 1)

    def test_copy_export_has_a_preflight_checklist(self):
        _, body, hdr = self.get("/export-copy.txt")
        self.assertIn("text/plain", hdr["Content-Type"])
        self.assertIn("BEFORE YOU SEND", body)
        self.assertIn("holdout", body.lower())
        self.assertIn("No customer numbers", body)

    def test_reset_returns_to_the_demo(self):
        self.post_multipart("/upload", {"consent": "1"},
                            {"f_main": ("s.csv", D.sample_simple(200))})
        self.assertEqual(S.STATE["source"], "csv")
        self.post("/reset")
        self.assertEqual(S.STATE["source"], "demo")
        self.assertFalse(S.STATE["connected"])
        self.assertEqual(S.result()["summary"]["ready"], R["summary"]["ready"])

    def test_inbox_category_filter(self):
        _, body, _ = self.get("/inbox?cat=free_item")
        self.assertTrue("Showing" in body)
        _, body, _ = self.get("/inbox?cat=does_not_exist")
        self.assertEqual(body.count('<details class="msg"'), 0)

    def test_state_survives_a_restart(self):
        self.post("/triggers", th_lapsed_days="150")
        self.post("/sending", tool="brevo")
        fresh = S.fresh_state()
        loaded = store.load()
        self.assertEqual(loaded["thresholds"]["lapsed_days"], 150)
        self.assertEqual(loaded["sender"]["tool"], "brevo")
        self.assertNotEqual(loaded, fresh)


# ---------------------------------------------------------------------------
# security primitives
# ---------------------------------------------------------------------------

class TestSecurityUnit(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        h = SEC.hash_secret("correct-horse-battery")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        self.assertTrue(SEC.verify_secret("correct-horse-battery", h))
        self.assertFalse(SEC.verify_secret("wrong", h))
        self.assertNotEqual(h, SEC.hash_secret("correct-horse-battery"))  # random salt

    def test_secretbox_roundtrip_and_tamper(self):
        b = SEC.SecretBox(master="unit-test-master-key")
        tok = b.encrypt('{"access_token": "secret"}')
        self.assertTrue(tok.startswith("enc:v1:"))
        self.assertNotIn("secret", tok)
        self.assertEqual(b.decrypt(tok), '{"access_token": "secret"}')
        bad = tok[:-4] + ("0000" if tok[-4:] != "0000" else "1111")
        with self.assertRaises(ValueError):
            b.decrypt(bad)
        with self.assertRaises(ValueError):
            SEC.SecretBox(master="a-different-key").decrypt(tok)

    def test_secretbox_keyfile(self):
        with tempfile.TemporaryDirectory() as td:
            kf = os.path.join(td, "m.key")
            b = SEC.SecretBox(keyfile=kf)
            self.assertEqual(b.source, "keyfile-created")
            self.assertEqual(os.stat(kf).st_mode & 0o777, 0o600)
            tok = b.encrypt("x")
            b2 = SEC.SecretBox(keyfile=kf)
            self.assertEqual(b2.source, "keyfile")
            self.assertEqual(b2.decrypt(tok), "x")

    def test_secretbox_legacy_passthrough(self):
        self.assertEqual(SEC.SecretBox(master="k").decrypt("plaintext"), "plaintext")

    def test_guard_rate_limit_and_lockout(self):
        g = SEC.Guard()
        for _ in range(SEC.RATE_POST):
            self.assertTrue(g.allow("9.9.9.9", "POST", SEC.RATE_POST))
        self.assertFalse(g.allow("9.9.9.9", "POST", SEC.RATE_POST))
        self.assertTrue(g.allow("8.8.8.8", "POST", SEC.RATE_POST))   # per-address
        for _ in range(SEC.LOGIN_MAX_FAILS - 1):
            self.assertFalse(g.note_login_fail("7.7.7.7"))
        self.assertTrue(g.note_login_fail("7.7.7.7"))                # 5th locks
        self.assertTrue(g.locked_until("7.7.7.7"))
        self.assertFalse(g.allow("7.7.7.7", "GET", SEC.RATE_GET))    # locked: all refused

    def test_audit_chain_and_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.log")
            log = SEC.AuditLog(path)
            for i in range(5):
                log.write("login_ok", ip="1.2.3.4", detail={"i": i})
            ok, broken_at, n = log.verify()
            self.assertTrue(ok)
            self.assertIsNone(broken_at)
            self.assertEqual(n, 5)
            self.assertEqual(len(log.read(3)), 3)
            lines = open(path).read().splitlines()
            lines[2] = lines[2].replace("login_ok", "logout")        # silent edit
            open(path, "w").write("\n".join(lines) + "\n")
            ok, broken_at, _ = SEC.AuditLog(path).verify()
            self.assertFalse(ok)
            self.assertEqual(broken_at, 2)

    def test_csrf_and_sessions(self):
        sess = SEC.new_session()
        self.assertTrue(SEC.csrf_ok(sess, sess["csrf"]))
        self.assertFalse(SEC.csrf_ok(sess, "nope"))
        self.assertFalse(SEC.csrf_ok(sess, ""))
        self.assertFalse(SEC.csrf_ok(None, sess["csrf"]))
        self.assertTrue(SEC.session_valid(sess))
        self.assertFalse(SEC.session_valid(dict(sess, expires=0)))
        self.assertFalse(SEC.session_valid(None))

    def test_security_headers(self):
        h = SEC.security_headers()
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertNotIn("Strict-Transport-Security", h)
        self.assertIn("Strict-Transport-Security", SEC.security_headers(tls=True))


# ---------------------------------------------------------------------------
# Square OAuth against a mock token server
# ---------------------------------------------------------------------------

class _MockSquare(BaseHTTPRequestHandler):
    tokens_response = {"access_token": "sqst_at", "refresh_token": "sqst_rt",
                       "merchant_id": "MLX1", "expires_in": 3600,
                       "scopes": ["ORDERS_READ"]}
    fail = False
    revoked = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/oauth2/token/revoke":
            type(self).revoked.append(payload)
            data = {}
        elif type(self).fail:
            data = {"error": "invalid_grant", "error_description": "code expired"}
        else:
            data = dict(type(self).tokens_response)
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestOAuthUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockSquare)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._env = {k: os.environ.get(k) for k in
                    ("SQUARE_CLIENT_ID", "SQUARE_CLIENT_SECRET", "SQUARE_OAUTH_BASE")}
        os.environ["SQUARE_CLIENT_ID"] = "sq_cid"
        os.environ["SQUARE_CLIENT_SECRET"] = "sq_csec"
        os.environ["SQUARE_OAUTH_BASE"] = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        _MockSquare.fail = False
        _MockSquare.revoked = []

    def test_configured_and_authorize_url(self):
        self.assertTrue(OAUTH.configured())
        url = OAUTH.authorize_url("st8te")
        self.assertIn("/oauth2/authorize", url)
        q = parse.parse_qs(parse.urlparse(url).query)
        self.assertEqual(q["client_id"], ["sq_cid"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["state"], ["st8te"])
        self.assertIn("ORDERS_READ", q["scope"][0])
        self.assertIn("CUSTOMERS_READ", q["scope"][0])

    def test_exchange_refresh_revoke(self):
        toks = OAUTH.exchange_code("code123")
        self.assertEqual(toks["access_token"], "sqst_at")
        self.assertEqual(toks["merchant_id"], "MLX1")
        self.assertGreater(toks["expires_at"], 0)
        refreshed = OAUTH.refresh(toks)
        self.assertEqual(refreshed["access_token"], "sqst_at")
        self.assertTrue(OAUTH.revoke(toks))

    def test_exchange_failure_raises(self):
        _MockSquare.fail = True
        with self.assertRaises(ValueError):
            OAUTH.exchange_code("bad-code")

    def test_status_states(self):
        self.assertEqual(OAUTH.status(None)["state"], "not_connected")
        toks = OAUTH.exchange_code("c")
        self.assertEqual(OAUTH.status(toks)["state"], "connected")
        self.assertEqual(OAUTH.status(dict(toks, expires_at=0))["state"], "expired")


# ---------------------------------------------------------------------------
# region: footers, hemispheres, spelling
# ---------------------------------------------------------------------------

class TestRegionEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = D.build()

    def _one(self, region, address=""):
        g = dict(D.DEFAULT_GOALS)
        g["_region"] = region
        g["_address"] = address
        r = E.run(self.ds, g, g)
        self.assertTrue(r["messages"])
        return r["messages"][0]

    def test_us_footer_has_address_and_commercial_line(self):
        m = self._one("US", "412 Smith St, Fitzroy VIC 3065")
        self.assertIn("412 Smith St", m["body"])
        self.assertIn("commercial email", m["body"])
        self.assertIn("10 business days", m["body"])
        self.assertNotIn("ABN", m["body"])

    def test_us_footer_without_address_is_flagged(self):
        self.assertIn("street address required by CAN-SPAM", self._one("US", "")["body"])

    def test_au_footer_unchanged(self):
        m = self._one("AU")
        self.assertIn(f"ABN {D.VENUE['abn']}", m["body"])
        self.assertNotIn("CAN-SPAM", m["body"])
        self.assertNotIn("commercial email", m["body"])

    def test_seasons_follow_hemisphere(self):
        now = datetime(2026, 11, 4)
        self.assertEqual(E._season_phrase("2026-07-15", now, "AU"), "back in winter")
        self.assertEqual(E._season_phrase("2026-07-15", now, "US"), "back in summer")
        self.assertEqual(E._season_phrase("2026-10-20", now, "US"), "earlier this season")

    def test_us_spelling_in_customer_copy(self):
        self.assertEqual(E._localise("organising a table", "US"), "organizing a table")
        self.assertEqual(E._localise("organising a table", "AU"), "organising a table")
        self.assertEqual(E._localise("your favourite", "US"), "your favorite")


# ---------------------------------------------------------------------------
# HTTP: password gate, sessions, CSRF, lockouts, headers, oversize
# ---------------------------------------------------------------------------

class TestHTTPSecurity(unittest.TestCase):
    PW = "correct-horse-battery"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.PATH
        store.PATH = os.path.join(cls._tmp.name, "state.json")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        store.PATH = cls._old_path
        cls._tmp.cleanup()

    def setUp(self):
        S.STATE.update(S.fresh_state())
        S.STATE["connected"] = True
        S.STATE["password_hash"] = SEC.hash_secret(self.PW)
        S.STATE["sessions"] = {}
        S.GUARD.locked.clear()
        S.GUARD.fails.clear()
        S.GUARD.hits.clear()
        S.FLASH.clear()
        S.persist()

    def tearDown(self):
        S.STATE.update(S.fresh_state())
        S.GUARD.locked.clear()
        S.GUARD.fails.clear()
        S.GUARD.hits.clear()
        S.FLASH.clear()
        S.persist()

    def get(self, path, cookie=None):
        req = request.Request(self.base + path,
                              headers={"Cookie": cookie} if cookie else {})
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def post(self, path, cookie=None, **form):
        body = parse.urlencode(form, doseq=True).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        req = request.Request(self.base + path, data=body, headers=headers)
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def login(self, pw=None):
        st, _, hdr = self.post("/login", password=pw or self.PW)
        self.assertEqual(st, 303)
        cookie = hdr["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        return cookie.split(";")[0]

    def csrf_of(self, cookie):
        _, body, _ = self.get("/", cookie)
        m = re.search(r'name="csrf" value="([^"]+)"', body)
        self.assertTrue(m, "csrf field missing from forms")
        return m.group(1)

    def test_gate_redirects_anonymous(self):
        st, _, hdr = self.get("/")
        self.assertEqual((st, hdr["Location"]), (303, "/login"))
        st, _, hdr = self.get("/inbox")
        self.assertEqual((st, hdr["Location"]), (303, "/login?next=/inbox"))
        st, _, _ = self.post("/goals", priority="repeat")
        self.assertEqual(st, 403)

    def test_login_page_and_healthz_stay_open(self):
        st, body, _ = self.get("/login")
        self.assertEqual(st, 200)
        self.assertIn("Sign in", body)
        st, body, _ = self.get("/healthz")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["auth_enabled"])

    def test_wrong_password_then_lockout(self):
        for _ in range(SEC.LOGIN_MAX_FAILS):
            st, _, hdr = self.post("/login", password="nope-nope-nope")
            self.assertEqual((st, hdr["Location"]), (303, "/login"))
        st, _, _ = self.post("/login", password="nope-nope-nope")
        self.assertEqual(st, 429)                       # locked out
        st, _, _ = self.get("/security")
        self.assertEqual(st, 429)                       # lockout blocks everything
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("login_fail", events)
        self.assertIn("lockout", events)

    def test_session_csrf_and_region_flow(self):
        cookie = self.login()
        st, body, _ = self.get("/", cookie)
        self.assertEqual(st, 200)
        self.assertIn('name="csrf"', body)
        st, _, _ = self.post("/region", cookie, region="US", venue_address="x")
        self.assertEqual(st, 403)                       # no CSRF token
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("csrf_reject", events)
        tok = self.csrf_of(cookie)
        st, _, hdr = self.post("/region", cookie, csrf=tok, region="US",
                               venue_address="412 Smith St, Fitzroy VIC 3065")
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        self.assertEqual(S.STATE["region"], "US")
        m = S.result()["messages"][0]
        self.assertIn("412 Smith St", m["body"])
        self.assertIn("commercial email", m["body"])
        st, _, _ = self.get("/export.csv", cookie)      # exports audited
        self.assertEqual(st, 200)
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("export", events)

    def test_us_region_requires_address(self):
        cookie = self.login()
        tok = self.csrf_of(cookie)
        st, _, hdr = self.post("/region", cookie, csrf=tok, region="US",
                               venue_address="")
        self.assertEqual(hdr["Location"], "/")
        self.assertEqual(S.STATE["region"], "AU")       # refused, nothing changed

    def test_logout_kills_session(self):
        cookie = self.login()
        tok = self.csrf_of(cookie)
        st, _, hdr = self.post("/logout", cookie, csrf=tok)
        self.assertEqual((st, hdr["Location"]), (303, "/login"))
        self.assertIn("Max-Age=0", hdr["Set-Cookie"])
        st, _, hdr = self.get("/", cookie)
        self.assertEqual((st, hdr["Location"]), (303, "/login"))

    def test_security_page_and_chain_verify(self):
        st, _, hdr = self.get("/security")
        self.assertEqual((st, hdr["Location"]), (303, "/login?next=/security"))
        cookie = self.login()
        st, body, _ = self.get("/security", cookie)
        self.assertEqual(st, 200)
        self.assertIn("Audit chain", body)
        tok = self.csrf_of(cookie)
        st, _, _ = self.post("/security/verify", cookie, csrf=tok)
        self.assertEqual(st, 303)
        _, body, _ = self.get("/security", cookie)
        self.assertIn("Audit chain verified", body)

    def test_response_headers_on_every_page(self):
        _, _, hdr = self.get("/login")
        self.assertEqual(hdr["X-Content-Type-Options"], "nosniff")
        self.assertEqual(hdr["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", hdr["Content-Security-Policy"])
        self.assertEqual(hdr["Cache-Control"], "no-store")

    def test_oversize_body_rejected(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port,
                                          timeout=30)
        conn.putrequest("POST", "/upload")
        conn.putheader("Content-Length", str(SEC.MAX_BODY_BYTES + 1))
        conn.endheaders()
        r = conn.getresponse()
        self.assertEqual(r.status, 413)
        r.read()
        conn.close()
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("oversize_upload", events)


# ---------------------------------------------------------------------------
# HTTP: the Square OAuth roundtrip through the running app
# ---------------------------------------------------------------------------

class TestHTTPOAuthFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), _MockSquare)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.PATH
        store.PATH = os.path.join(cls._tmp.name, "state.json")
        cls._env = {k: os.environ.get(k) for k in
                    ("SQUARE_CLIENT_ID", "SQUARE_CLIENT_SECRET", "SQUARE_OAUTH_BASE")}
        os.environ["SQUARE_CLIENT_ID"] = "sq_cid"
        os.environ["SQUARE_CLIENT_SECRET"] = "sq_csec"
        os.environ["SQUARE_OAUTH_BASE"] = f"http://127.0.0.1:{cls.mock.server_port}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.srv, cls.mock):
            srv.shutdown()
            srv.server_close()
        store.PATH = cls._old_path
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._tmp.cleanup()

    def setUp(self):
        S.STATE.update(S.fresh_state())      # demo mode: gate open, no CSRF needed
        S.STATE["connected"] = True
        S.GUARD.locked.clear()
        S.GUARD.fails.clear()
        S.GUARD.hits.clear()
        S.FLASH.clear()
        S.persist()
        _MockSquare.fail = False
        _MockSquare.revoked = []
        self._old_box = ST._BOX
        ST._BOX = SEC.SecretBox(master="test-master-key")

    def tearDown(self):
        ST._BOX = self._old_box
        S.STATE.update(S.fresh_state())
        S.persist()

    def get(self, path):
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(self.base + path, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def post(self, path, **form):
        body = parse.urlencode(form, doseq=True).encode()
        req = request.Request(self.base + path, data=body,
                              headers={"Content-Type":
                                       "application/x-www-form-urlencoded"})
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def test_full_oauth_roundtrip(self):
        st, _, hdr = self.get("/oauth/start")
        self.assertEqual(st, 303)
        loc = hdr["Location"]
        self.assertTrue(loc.startswith(
            os.environ["SQUARE_OAUTH_BASE"] + "/oauth2/authorize"), loc)
        state = parse.parse_qs(parse.urlparse(loc).query)["state"][0]
        st, _, hdr = self.get(f"/oauth/callback?code=authcode&state={state}")
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        enc = S.STATE["oauth_tokens_enc"]
        self.assertTrue(enc and enc.startswith("enc:v1:"))   # encrypted at rest
        self.assertEqual(S.STATE["source"], "square")
        toks = S._load_tokens()
        self.assertEqual(toks["access_token"], "sqst_at")
        self.assertEqual(toks["merchant_id"], "MLX1")
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("oauth_start", events)
        self.assertIn("oauth_ok", events)
        _, body, _ = self.get("/sending")
        self.assertIn("encrypted at rest", body)
        st, _, hdr = self.post("/oauth/revoke")
        self.assertEqual((st, hdr["Location"]), (303, "/sending"))
        self.assertIsNone(S.STATE["oauth_tokens_enc"])
        self.assertEqual(_MockSquare.revoked[-1]["refresh_token"], "sqst_rt")

    def test_callback_with_bogus_state_is_rejected(self):
        st, _, hdr = self.get("/oauth/callback?code=x&state=forged-state")
        self.assertEqual((st, hdr["Location"]), (303, "/sending"))
        self.assertIsNone(S.STATE["oauth_tokens_enc"])
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("oauth_fail", events)

    def test_callback_error_param(self):
        st, _, hdr = self.get("/oauth/callback?error=access_denied&state=whatever")
        self.assertEqual(hdr["Location"], "/sending")
        self.assertIsNone(S.STATE["oauth_tokens_enc"])

    def test_start_unconfigured_redirects_with_note(self):
        saved = os.environ.pop("SQUARE_CLIENT_ID")
        try:
            st, _, hdr = self.get("/oauth/start")
            self.assertEqual((st, hdr["Location"]), (303, "/sending"))
            self.assertIsNone(S.STATE["oauth_tokens_enc"])
        finally:
            os.environ["SQUARE_CLIENT_ID"] = saved


# ---------------------------------------------------------------------------
# modifiers: import -> habit -> copy, and the offer sense-guard
# ---------------------------------------------------------------------------

def _synthetic_customer(mod="Oat Milk", n=6, item="Flat White", gap=7, last=21):
    now = datetime(2026, 11, 4)
    orders = []
    for k in range(n):
        ts = now - timedelta(days=last + gap * k, hours=-9)
        orders.append({
            "id": f"syn_{k}", "at": ts.isoformat(), "date": ts.date().isoformat(),
            "weekday": ts.weekday(), "hour": 9, "daypart": "morning", "party_size": 1,
            "items": [{"name": item, "qty": 1, "mods": [mod] if mod else []}],
            "gross": 5.5, "discount": 0.0, "net": 5.5, "cost": 1.2, "channel": "DINE_IN",
        })
    return {"id": "syn_1", "name": "Sam Tester", "first_name": "Sam",
            "email": "sam@tester.com", "phone": None, "archetype": "test",
            "email_consent": True, "unsubscribed": False,
            "consent_source": "test", "consent_at": "2026-01-01",
            "birthday": None, "orders": orders, "joined": "2026-01-01"}


class TestModifierPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = D.build()
        E.set_menu(cls.ds["menu"])
        cls.now = datetime(2026, 11, 4)

    def test_profile_builds_habits_not_one_offs(self):
        p = E.profile(_synthetic_customer(), self.now)
        self.assertIn("Flat White", p["mod_habits"])
        mod, n, share = p["mod_habits"]["Flat White"][0]
        self.assertEqual((mod, n), ("Oat Milk", 6))
        self.assertGreaterEqual(share, 0.55)
        # a single trial visit does not make a habit
        c = _synthetic_customer(mod="Almond Milk", n=6)
        c["orders"][0]["items"][0]["mods"] = ["Decaf"]     # one-off
        p2 = E.profile(c, self.now)
        mods2 = [m for m, _, _ in p2["mod_habits"]["Flat White"]]
        self.assertNotIn("Decaf", mods2)

    def test_copy_names_the_choice_never_the_count(self):
        c = _synthetic_customer()
        p = E.profile(c, self.now)
        g = dict(D.DEFAULT_GOALS)
        ok, ev = E.t_signature_overdue(p, g, E.DEFAULT_THRESHOLDS)
        self.assertTrue(ok)
        self.assertIn("Oat Milk", ev["mods"])             # owner-side detail exists
        self.assertIn("%", ev["mods"])
        out = E.render("signature_overdue", ev, c, p, g)
        self.assertIn("(oat milk)", out["body"])          # customer sees the choice
        self.assertNotIn("%", out["body"])
        self.assertNotIn("of their orders", out["body"])
        self.assertNotIn("6 times", out["body"])

    def test_offer_contradiction_is_swapped_and_explained(self):
        c = _synthetic_customer(mod="Oat Milk")
        p = E.profile(c, self.now)
        g = dict(D.DEFAULT_GOALS)
        g["trigger_offer"]["signature_overdue"] = "upg_oat"   # "an oat milk upgrade"
        ok, ev = E.t_signature_overdue(p, g, E.DEFAULT_THRESHOLDS)
        out = E.render("signature_overdue", ev, c, p, g)
        self.assertNotEqual(out["offer_id"], "upg_oat")
        self.assertNotIn("oat milk upgrade", out["body"])
        self.assertIn("contradicted their actual habit", out["why"])
        # a non-contradicting customer keeps the merchant's chosen offer
        c2 = _synthetic_customer(mod="Extra Shot")
        p2 = E.profile(c2, self.now)
        ok2, ev2 = E.t_signature_overdue(p2, g, E.DEFAULT_THRESHOLDS)
        out2 = E.render("signature_overdue", ev2, c2, p2, g)
        self.assertEqual(out2["offer_id"], "upg_oat")
        self.assertNotIn("contradicted", out2["why"])

    def test_square_column_and_parenthetical_both_parse(self):
        hdr = "Created at,Order id,Customer id,Item variation name,Item modifier names,Sold count"
        rows = ("2026-09-01T09:00:00,o1,sam@t.com,Flat White,Oat Milk; Extra Shot,1\n"
                "2026-09-08T09:00:00,o2,sam@t.com,Flat White (Decaf),,1\n")
        visits, _, rep = IMP.parse_square(hdr + "\n" + rows, "", [])
        self.assertIn("modifiers", rep["fields_found"])
        self.assertEqual(visits[0]["items"][0]["mods"], ["Oat Milk", "Extra Shot"])
        self.assertEqual(visits[1]["items"][0]["name"], "Flat White")
        self.assertEqual(visits[1]["items"][0]["mods"], ["Decaf"])

    def test_simple_format_embedded_mods(self):
        csv_text = ("date,email,name,items\n"
                    "2026-09-01,sam@t.com,Sam,2x Flat White (Oat Milk); 1x Garlic Bread\n")
        visits, _, _ = IMP.parse_simple(csv_text, [])
        self.assertEqual(visits[0]["items"][0]["mods"], ["Oat Milk"])
        self.assertEqual(visits[0]["items"][1]["mods"], [])

    def test_menu_carries_common_mods(self):
        visits, _, _ = IMP.parse_square(
            "Created at,Order id,Customer id,Item variation name,Item modifier names,Sold count\n"
            "2026-09-01T09:00:00,o1,a@b.com,Flat White,Oat Milk,1\n"
            "2026-09-02T09:00:00,o2,a@b.com,Flat White,Oat Milk,1\n"
            "2026-09-03T09:00:00,o3,c@d.com,Flat White,Decaf,1\n", "", [])
        menu = IMP.build_menu(visits)
        fw = next(m for m in menu if m["name"] == "Flat White")
        self.assertEqual(fw["common_mods"][0], ("Oat Milk", 2))

    def test_sample_files_demonstrate_modifiers(self):
        tx, _ = D.sample_square(10)
        self.assertIn("Item modifier names", tx.splitlines()[0])
        self.assertTrue(any(l.split(",")[7] for l in tx.splitlines()[1:]))
        self.assertIn("(", D.sample_simple(120))

    def test_demo_run_uses_mods_in_signature_copy(self):
        g = dict(D.DEFAULT_GOALS)
        r = E.run(self.ds, g, g)
        sig = [m for m in r["messages"] if m["trigger_id"] == "signature_overdue"]
        with_mods = [m for m in sig if m["evidence"].get("mods")]
        self.assertTrue(with_mods, "no signature message carried modifier evidence")
        for m in with_mods:
            self.assertIn("(", m["body"].split("\n\n")[1])
            self.assertIsNone(re.search(r"\d+\s*(%|percent)", m["body"]))


# ---------------------------------------------------------------------------
# tone control
# ---------------------------------------------------------------------------

class TestToneControl(unittest.TestCase):
    BANNED = re.compile(r"\d+\s*(days?|weeks?|months?|times|visits|percent)", re.I)

    @classmethod
    def setUpClass(cls):
        cls.ds = D.build()
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.PATH
        store.PATH = os.path.join(cls._tmp.name, "state.json")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        store.PATH = cls._old_path
        cls._tmp.cleanup()

    def _get(self, path):
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(self.base + path, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def _post(self, path, **form):
        body = parse.urlencode(form, doseq=True).encode()
        req = request.Request(self.base + path, data=body,
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def _run(self, tone):
        g = dict(D.DEFAULT_GOALS)
        g["tone"] = tone
        return E.run(self.ds, g, g)

    def test_tones_are_distinct_and_rule_safe(self):
        base = self._run("warm")
        by_id = {m["id"]: m for m in base["messages"]}
        for tone in ("punchy", "polished", "playful"):
            r = self._run(tone)
            diff = sum(1 for m in r["messages"] if m["body"] != by_id[m["id"]]["body"])
            self.assertGreater(diff, len(r["messages"]) * 0.8,
                               f"{tone} tone barely changed the copy")
            for m in r["messages"]:
                self.assertIsNone(self.BANNED.search(m["body"]), f"{tone}: numbers leaked")
                self.assertIsNone(self.BANNED.search(m["subject"]), f"{tone}: numbers leaked")
                self.assertIn(m["customer"]["first_name"], m["subject"])
                self.assertIn("Unsubscribe", m["body"])      # footer survives every tone
                self.assertNotIn("we noticed", m["body"].lower())
            s = r["summary"]
            self.assertGreaterEqual(s["unique_subjects"] / s["ready"], 0.75,
                                    f"{tone}: subjects collapsed")

    def test_greetings_and_signoffs_per_tone(self):
        mid = None
        for tone, greet, sign in (("warm", "Hi ", "- Marlow & Finch"),
                                  ("punchy", "{name} -", "See you soon."),
                                  ("polished", "Hello ", "We would be glad to see you."),
                                  ("playful", "Hey ", "Your table misses you.")):
            r = self._run(tone)
            mid = mid or r["messages"][0]["id"]
            same = next(x for x in r["messages"] if x["id"] == mid)
            want = greet.format(name=same["customer"]["first_name"])
            self.assertTrue(same["body"].split("\n")[0].startswith(want),
                            f"{tone} greeting wrong: {same['body'].splitlines()[0]}")
            self.assertIn(sign, same["body"])

    def test_default_is_warm_and_tone_never_touches_footer(self):
        self.assertEqual(D.DEFAULT_GOALS["tone"], "warm")
        r = self._run("warm")
        r2 = self._run("playful")
        f1 = r["messages"][0]["body"].split("\n\n--\n")[1]
        f2 = r2["messages"][0]["body"].split("\n\n--\n")[1]
        self.assertEqual(f1, f2)

    def test_http_goals_sets_tone(self):
        original = S.STATE["goals"]
        S.STATE["goals"] = dict(original)
        try:
            st, _, hdr = self._post("/goals", tone="playful")
            self.assertEqual((st, hdr["Location"]), (303, "/triggers"))
            self.assertEqual(S.STATE["goals"]["tone"], "playful")
            _, body, _ = self._get("/inbox")
            self.assertEqual(body.count("Hey "), body.count("Hey "))   # page renders
            st, _, _ = self._post("/goals", tone="nonsense")
            self.assertEqual(S.STATE["goals"]["tone"], "playful")      # invalid ignored
            _, body, _ = self._get("/goals")
            self.assertIn('name="tone"', body)
            self.assertIn("Playful", body)
            self.assertIn('value="playful" checked', body)
        finally:
            S.STATE["goals"] = original
            S.persist()


# ---------------------------------------------------------------------------
# business identity: real data is never signed by the demo venue
# ---------------------------------------------------------------------------

VALID_ABN = "51824753556"        # the Australian Business Register's published example


class TestBusinessIdentity(_HTTPHarness, unittest.TestCase):
    DEMO_NAME = D.VENUE["name"]
    DEMO_ABN = D.VENUE["abn"]

    def upload(self):
        self.post_multipart("/upload", {"consent": "1"},
                            {"f_main": ("s.csv", D.sample_simple(300))})
        self.assertEqual(S.STATE["source"], "csv")

    def save(self, **form):
        fields = {"biz_name": "Juniper & Co", "biz_suburb": "Brunswick, Melbourne",
                  "region": "AU", "biz_abn": VALID_ABN, "venue_address": ""}
        fields.update(form)
        return self.post("/business", **fields)

    def assertNoDemoIdentity(self, text):
        self.assertNotIn(self.DEMO_NAME, text)
        self.assertNotIn(self.DEMO_ABN, text)

    def test_abn_check_digit(self):
        import abn
        self.assertEqual(abn.normalise(VALID_ABN), "51 824 753 556")
        self.assertEqual(abn.normalise("53 004 085 616"), "53 004 085 616")
        for bad in ("51 824 753 557", "12 345 678 901", "5182475355", "", "abc",
                    "01824753556"):
            self.assertIsNone(abn.normalise(bad), bad)

    def test_demo_data_keeps_the_demo_signature(self):
        for m in S.result()["messages"]:
            self.assertIn(f"- {self.DEMO_NAME}", m["body"])
        st, _, _ = self.get("/export.csv")
        self.assertEqual(st, 200)

    def test_setup_shows_the_business_form(self):
        _, body, _ = self.get("/")
        self.assertIn('action="/business"', body)
        self.assertIn('name="biz_abn"', body)

    def test_real_data_without_details_uses_placeholders_and_blocks(self):
        self.upload()
        msgs = S.result()["messages"]
        self.assertGreater(len(msgs), 0)
        for m in msgs:
            self.assertIn("- [Your business name]", m["body"])
            self.assertIn("ABN [ABN]", m["body"])
            self.assertNoDemoIdentity(m["body"])
        for path in ("/export.csv", "/export-copy.txt"):
            st, _, hdr = self.get(path)
            self.assertEqual((st, hdr["Location"]), (303, "/"), path)
        st, _, hdr = self.post("/approve?id=" + parse.quote(msgs[0]["id"]))
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        self.assertEqual(S.STATE["sent"], [])
        _, body, _ = self.get("/")
        self.assertIn("approving and exporting are switched off", body)
        _, body, _ = self.get("/inbox")
        self.assertIn("Your business details are missing", body)

    def test_saved_details_sign_every_message_and_export(self):
        self.upload()
        st, _, hdr = self.save()
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        self.assertEqual(S.STATE["business"], {"name": "Juniper & Co",
                                               "suburb": "Brunswick, Melbourne",
                                               "abn": "51 824 753 556"})
        msgs = S.result()["messages"]
        for m in msgs:
            self.assertIn("- Juniper & Co", m["body"])
            self.assertIn("Juniper & Co - Brunswick, Melbourne", m["body"])
            self.assertIn("ABN 51 824 753 556", m["body"])
            self.assertNotIn("[Your business name]", m["body"])
            self.assertNoDemoIdentity(m["body"])
        st, csv_text, _ = self.get("/export.csv")
        self.assertEqual(st, 200)
        self.assertIn("Juniper & Co", csv_text)
        self.assertNoDemoIdentity(csv_text)
        st, txt, _ = self.get("/export-copy.txt")
        self.assertEqual(st, 200)
        self.assertIn("ABN in the footer is correct: 51 824 753 556", txt)
        self.assertNoDemoIdentity(txt)
        st, _, hdr = self.post("/approve?id=" + parse.quote(msgs[0]["id"]), approver="Sam")
        self.assertEqual(hdr["Location"], "/inbox")
        self.assertEqual(len(S.STATE["sent"]), 1)
        self.assertNoDemoIdentity(S.STATE["sent"][0]["body"])

    def test_bad_or_missing_details_are_refused_whole(self):
        self.save(biz_abn="51 824 753 557")
        self.assertEqual(S.STATE["business"]["name"], "")
        _, body, _ = self.get("/")
        self.assertIn("fails the ABN check digit", body)
        self.save(biz_name="  ")
        self.assertEqual(S.STATE["business"]["name"], "")
        self.save(biz_abn="")
        self.assertEqual(S.STATE["business"]["name"], "")

    def test_us_needs_an_address_not_an_abn(self):
        self.upload()
        self.save(region="US", biz_abn="", venue_address="")
        self.assertEqual(S.STATE["region"], "AU")       # refused, nothing changed
        self.assertEqual(S.STATE["business"]["name"], "")
        self.save(region="US", biz_abn="", venue_address="1 Main St, Springfield IL 62701")
        self.assertEqual(S.STATE["region"], "US")
        body = S.result()["messages"][0]["body"]
        self.assertIn("- Juniper & Co", body)
        self.assertIn("1 Main St, Springfield IL 62701", body)
        self.assertNotIn("ABN", body)
        st, _, _ = self.get("/export.csv")
        self.assertEqual(st, 200)

    def test_tone_sign_off_uses_the_saved_name(self):
        self.save()
        S.STATE["goals"]["tone"] = "playful"
        body = S.result()["messages"][0]["body"]
        self.assertIn(E.TONE_SIGNOFF["playful"].format(venue="Juniper & Co"), body)
        self.assertNoDemoIdentity(body)

    def test_no_screen_shows_the_demo_name_on_real_data(self):
        self.upload()
        mid = S.result()["messages"][0]["id"]
        pages = ("/", "/goals", "/triggers", "/inbox", "/sending", "/sent", "/data",
                 "/security", "/m?id=" + parse.quote(mid))
        for saved in (False, True):
            if saved:
                self.save()
            for path in pages:
                st, body, _ = self.get(path)
                self.assertEqual(st, 200, path)
                self.assertNotIn("Marlow", body, f"{path} (details saved: {saved})")
        _, body, _ = self.get("/inbox")
        self.assertIn("Juniper &amp; Co, Brunswick", body)        # page header

    def test_details_persist_survive_reset_and_are_audited(self):
        self.save()
        self.assertEqual(store.load()["business"]["abn"], "51 824 753 556")
        self.post("/reset")
        self.assertEqual(S.STATE["business"]["name"], "Juniper & Co")
        saved = [ev for ev in S.AUDIT.read(500)
                 if ev["event"] == "config_change" and ev["detail"].get("what") == "business"]
        self.assertTrue(saved)


# ---------------------------------------------------------------------------
# owner setup link: a provisioned instance is shut until its owner claims it
# ---------------------------------------------------------------------------

SETUP_TOKEN = "test-setup-token-Zq8LxWm3kP0vRt7yHn2c"


class TestSetupLink(_HTTPHarness, unittest.TestCase):
    PASSWORD = "correct horse battery"

    def setUp(self):
        super().setUp()
        os.environ["REGULARS_SETUP_TOKEN"] = SETUP_TOKEN
        for d in (S.GUARD.locked, S.GUARD.fails, S.GUARD.hits):
            d.clear()

    def tearDown(self):
        os.environ.pop("REGULARS_SETUP_TOKEN", None)
        for d in (S.GUARD.locked, S.GUARD.fails, S.GUARD.hits):
            d.clear()
        super().tearDown()

    def call(self, method, path, cookie=None, **form):
        data = parse.urlencode(form, doseq=True).encode() if method == "POST" else None
        req = request.Request(self.base + path, data=data, method=method)
        if cookie:
            req.add_header("Cookie", cookie)
        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, r.read().decode(), r.headers
        except HTTPError as err:
            return err.code, err.read().decode(), err.headers

    def claim(self, token=SETUP_TOKEN, pw=None, pw2=None):
        pw = pw or self.PASSWORD
        return self.call("POST", "/welcome", token=token, password=pw,
                         password2=pw2 if pw2 is not None else pw)

    def test_unclaimed_instance_is_shut(self):
        for path in ("/", "/inbox", "/goals", "/export.csv", "/sample/simple.csv", "/login",
                     "/security"):
            st, body, _ = self.call("GET", path)
            self.assertEqual(st, 403, path)
            self.assertIn("set up yet", body, path)
            self.assertNotIn("Marlow", body, path)        # no demo name, no menu
            self.assertNotIn('href="/goals"', body, path)
        st, body, _ = self.call("GET", "/healthz")
        self.assertEqual(st, 200)
        for path, form in (("/goals", {"priority": "fill_quiet"}), ("/connect", {}),
                           ("/login", {"password": "x" * 12, "password2": "x" * 12})):
            st, _, _ = self.call("POST", path, **form)
            self.assertEqual(st, 403, path)
        self.assertIsNone(S.STATE["password_hash"])      # localhost can't bypass it
        self.assertEqual(S.STATE["goals"]["priority"], "repeat")

    def test_wrong_token_is_refused_audited_and_locks_out(self):
        st, body, _ = self.call("GET", "/welcome?token=nope")
        self.assertEqual(st, 403)
        self.assertIn("isn&rsquo;t valid", body)
        st, _, _ = self.claim(token=SETUP_TOKEN[:-1] + "X")
        self.assertEqual(st, 403)
        self.assertIsNone(S.STATE["password_hash"])
        events = [ev["event"] for ev in S.AUDIT.read(500)]
        self.assertIn("setup_fail", events)
        for _ in range(SEC.LOGIN_MAX_FAILS):
            self.call("GET", "/welcome?token=guess")
        st, _, _ = self.call("GET", "/welcome?token=" + SETUP_TOKEN)
        self.assertEqual(st, 429)                         # even the right one, once locked

    def test_link_sets_password_signs_in_and_burns(self):
        st, body, _ = self.call("GET", "/welcome?token=" + SETUP_TOKEN)
        self.assertEqual(st, 200)
        self.assertIn('action="/welcome"', body)
        self.assertNotIn("Marlow", body)
        st, _, hdr = self.claim()
        self.assertEqual((st, hdr["Location"]), (303, "/"))
        cookie = hdr["Set-Cookie"].split(";")[0]
        self.assertTrue(cookie.startswith("REGULARS_SID="))
        self.assertTrue(SEC.verify_secret(self.PASSWORD, S.STATE["password_hash"]))
        self.assertTrue(store.load()["setup_burned"])
        st, body, _ = self.call("GET", "/", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn("Next: add your business details", body)
        st, _, _ = self.call("GET", "/")                  # no cookie: now a normal gate
        self.assertEqual(st, 303)
        events = [ev for ev in S.AUDIT.read(500) if ev["event"] == "setup_ok"]
        self.assertEqual(events[-1]["detail"]["via"], "setup_link")
        # the link is spent: no form, and a second claim changes nothing
        before = S.STATE["password_hash"]
        st, body, _ = self.call("GET", "/welcome?token=" + SETUP_TOKEN)
        self.assertIn("already set up", body)
        self.assertNotIn('name="password"', body)
        self.claim(pw="another password!")
        self.assertEqual(S.STATE["password_hash"], before)

    def test_burned_token_cannot_reopen_a_reset_instance(self):
        self.claim()
        S.STATE["password_hash"] = None                   # operator recovery on the server
        S.STATE["sessions"] = {}
        st, _, _ = self.call("GET", "/welcome?token=" + SETUP_TOKEN)
        self.assertEqual(st, 403)                         # same token: burned
        os.environ["REGULARS_SETUP_TOKEN"] = SETUP_TOKEN + "-new"
        st, _, hdr = self.claim(token=SETUP_TOKEN + "-new")
        self.assertEqual((st, hdr["Location"]), (303, "/"))

    def test_weak_password_keeps_the_link_live(self):
        st, body, _ = self.claim(pw="short")
        self.assertEqual(st, 200)
        self.assertIn("Password not set", body)
        st, body, _ = self.claim(pw2="something else entirely")
        self.assertIn("Password not set", body)
        self.assertIsNone(S.STATE["password_hash"])
        st, _, _ = self.claim()
        self.assertEqual(st, 303)

    def test_token_never_reaches_the_logs(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            self.call("GET", "/welcome?token=" + SETUP_TOKEN)
            self.call("GET", "/welcome?token=wrong-guess")
        finally:
            sys.stderr = old
        self.assertNotIn(SETUP_TOKEN, err.getvalue())
        self.assertIn("token=[redacted]", err.getvalue())
        with open(S.AUDIT.path, encoding="utf-8") as f:
            self.assertNotIn(SETUP_TOKEN, f.read())

    def test_no_token_configured_changes_nothing(self):
        os.environ.pop("REGULARS_SETUP_TOKEN", None)
        st, _, _ = self.call("GET", "/")
        self.assertEqual(st, 200)                         # open demo mode, as before
        st, _, hdr = self.call("GET", "/welcome?token=anything")
        self.assertEqual((st, hdr["Location"]), (303, "/login"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
