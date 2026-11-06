"""
The engine: customer behaviour in, one specific message out.

Three ideas, in order of importance:

1. Every offer is derived from what this customer actually does. Nobody gets
   "here's $20 off" unless $20 off is genuinely the right lever for them.

2. Triggers are evaluated in priority order and the FIRST match wins, so each
   person gets exactly one message - the most specific one that applies. A
   customer who is both a lapsed VIP and a birthday person gets the birthday
   message, because it's more personal and more time-sensitive.

3. If no trigger matches, the person gets nothing. Sending to everyone is what
   makes response rates collapse and gets venues unsubscribed. A short,
   well-matched list beats a long generic one every time.

Nothing here calls an LLM. Every message is assembled from the customer's own
data and the merchant's own offer settings, so every dollar figure and every
claim in a message can be traced to a Square order. That is not a limitation -
it is the reason an operator can trust it enough to press send.

This file is the part that is the same in every industry: suppression, trigger
ordering, offer selection, tone, regional spelling and the legal footer. What
a customer's behaviour means - the profile, the triggers, the words - comes
from the active industry pack (packs/, chosen with REGULARS_INDUSTRY).
"""

from collections import Counter

import packs
from core import (DAY_NAMES, DAY_SHORT, _dt, _localise, _median,  # noqa: F401
                  _season_phrase, _vague, _vague_future)

PACK = packs.load()

# The active pack's vocabulary, under the names the screens and tests use.
DEFAULT_THRESHOLDS = PACK.DEFAULT_THRESHOLDS
THRESHOLD_FIELDS = PACK.THRESHOLD_FIELDS
TRIGGERS = PACK.TRIGGERS
OFFER_CATEGORIES = PACK.OFFER_CATEGORIES
profile = PACK.profile
venue_stats = PACK.venue_stats
explain_no_match = PACK.explain_no_match


def __getattr__(name):
    """Anything else pack-specific (DAYPART_LABEL, set_menu, a trigger
    function) is looked up on the active pack."""
    return getattr(PACK, name)


PRIORITY_BONUS = 8


def _spend_percentile_fn(customers, now):
    spends = sorted(sum(o["net"] for o in c["orders"]) for c in customers if c["orders"])
    if not spends:
        return lambda x: 50

    def fn(v):
        lo, hi = 0, len(spends)
        while lo < hi:
            mid = (lo + hi) // 2
            if spends[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        return round(lo / len(spends) * 100)
    return fn


# ---------------------------------------------------------------------------
# 2. SUPPRESSION — who must not be emailed, whatever else is true
# ---------------------------------------------------------------------------

def suppression_reason(c, p, settings, sent_history, now):
    if not c.get("email"):
        return ("no_contact", "No email address on file. Square never exposes cardholder "
                              "contact details, so this person can only be reached if staff "
                              "capture their email at the register.")
    if c.get("unsubscribed"):
        return ("unsubscribed", "Previously unsubscribed. Suppression is permanent and cannot "
                                "be overridden by any trigger.")
    if not c.get("email_consent"):
        return ("no_consent", "Has an email address but no record of marketing consent. Under "
                              "the Spam Act a card payment is not consent. Quarantined until "
                              "re-permissioned.")
    if p.get("empty"):
        return ("no_history", "No order history, so there is nothing to match an offer against.")
    if p["recency_days"] <= settings["skip_if_visited_within_days"]:
        when = "today" if p["recency_days"] == 0 else f"{p['recency_days']} day(s) ago"
        return ("just_visited", f"Was in {when}. They do not need an incentive - "
                                f"leave them alone.")
    last_sent = sent_history.get(c["id"])
    if last_sent and (now - _dt(last_sent)).days < settings["min_days_between_sends"]:
        gap = (now - _dt(last_sent)).days
        return ("too_soon", f"Was sent a message {gap} days ago. Frequency cap is "
                            f"{settings['min_days_between_sends']} days.")
    return None


def _ordered_triggers(goals):
    """Rank triggers: time-critical first, then specificity, with the
    merchant's stated priority nudging relevant triggers up.

    The priority is a bonus, not an override. Letting it reorder wholesale
    would let a vague trigger ("their visits are drifting") leapfrog a specific
    one ("you always book the table for six") just because the merchant picked
    retention that week. Specificity is what makes the message land.
    """
    prio = goals.get("priority", "repeat")

    def key(t):
        band = 0 if t.get("always_top") else 1
        bonus = PRIORITY_BONUS if prio in t["serves"] else 0
        return (band, -(t["weight"] + bonus))

    return sorted(PACK.TRIGGERS, key=key)


def offer_for(goals, trigger_id):
    """The offer this trigger will use, from the merchant's catalogue."""
    cat = next(t["offer_cat"] for t in PACK.TRIGGERS if t["id"] == trigger_id)
    oid = goals.get("trigger_offer", {}).get(trigger_id)
    for o in goals.get("catalog", []):
        if o["id"] == oid:
            return o
    for o in goals.get("catalog", []):
        if o["cat"] == cat:
            return o
    return {"id": "fallback", "cat": cat, "label": "something on us", "cost": 0.0}


def _footer(goals):
    v = goals.get("_venue") or PACK.VENUE
    tag = goals.get("unsub_tag") or "{{unsubscribe_url}}"
    region = goals.get("_region", "AU")
    lines = [f"\n\n--\n{v['name']} - {v['suburb']}"]
    if region == "US":
        # CAN-SPAM 15 U.S.C. 7704(a)(5): a commercial email must carry a valid
        # physical postal address, and the opt-out must be honoured for at
        # least 30 days after sending. AU's Spam Act requires neither.
        addr = (goals.get("_address") or "").strip()
        lines.append(addr or "[street address required by CAN-SPAM - set it on the "
                              "Setup screen]")
        lines.append("This is a commercial email. You can opt out at any time and your "
                     "opt-out will be honoured within 10 business days.")
    else:
        lines.append(f"ABN {v['abn']}")
    lines.append("You're getting this because you gave us your email at the venue or when "
                 "you signed up to our loyalty program.")
    lines.append(f"Unsubscribe: {tag} - takes one click, and it's permanent.")
    lines.append("Questions? Just reply to this email.")
    return "\n".join(lines)


TONES = {
    "warm": "Warm & neighbourly (the default)",
    "punchy": "Short & punchy",
    "polished": "Polished & premium",
    "playful": "Playful & cheeky",
}

TONE_GREETING = {
    "warm": "Hi {first},",
    "punchy": "{first} -",
    "polished": "Hello {first},",
    "playful": "Hey {first}!",
}

TONE_SIGNOFF = {
    "warm": "- {venue}",
    "punchy": "See you soon.\n- {venue}",
    "polished": "We would be glad to see you.\n- {venue}",
    "playful": "Your table misses you. (So do we.)\n- {venue}",
}

# Phrase-level swaps at the seams of the templates. Tone never touches: the
# evidence rules, the offer itself, the vague-timing discipline, the footer.
TONE_SWAPS = {
    "warm": [],
    "punchy": [
        ("Whenever you're next in,", "Next time in:"),
        ("we've missed handing it over", "we miss handing it over"),
        ("we'd like to make it easy", "let's make it easy"),
        ("No pressure and no hard feelings - life gets busy.", "No pressure. Life gets busy."),
        ("we'd love your vote of confidence", "we want your vote of confidence"),
        ("Thanks for coming in recently - genuinely, new faces are the whole reason "
         "we do this.", "Thanks for coming in. New faces are why we do this."),
    ],
    "polished": [
        ("Whenever you're next in,", "On your next visit,"),
        ("we've missed handing it over", "it has been a pleasure to prepare"),
        ("no code, no app, just mention it when you order",
         "simply mention this note when ordering"),
        ("we'd like to make it easy", "we would like to make it effortless"),
        ("and it might become your thing", "and it may become your new ritual"),
        ("we'd love your vote of confidence", "we would value your vote of confidence"),
    ],
    "playful": [
        ("Whenever you're next in,", "When next the mood strikes,"),
        ("we've missed handing it over", "the espresso machine has been asking after you"),
        ("we'd like to make it easy", "we'd like to make it dangerously easy"),
        ("the room is quieter for it", "the room has been conspicuously bereft"),
        ("honestly they're our favourite kind of night",
         "honestly they're our favourite kind of chaos"),
    ],
}


def _apply_tone(subj, body, tone, first, venue):
    tone = tone if tone in TONES else "warm"
    if tone == "warm":
        return subj, body
    body = body.replace(f"Hi {first},", TONE_GREETING[tone].format(first=first), 1)
    body = body.replace(f"- {venue}", TONE_SIGNOFF[tone].format(venue=venue), 1)
    for a, b in TONE_SWAPS.get(tone, []):
        body = body.replace(a, b)
    return subj, body


def render(trigger_id, ev, c, p, goals, stats=None, now=None):
    """Assemble one message: pick the offer, let the industry pack write the
    words, then apply tone, regional spelling and the legal footer."""
    first = c["first_name"]
    offer = offer_for(goals, trigger_id)
    offer_note = ""
    # Sense check: an offer must not contradict the person it's aimed at.
    # Offering "an oat milk upgrade" to someone whose usual already IS oat
    # milk is the kind of mistake that proves nobody is reading. If their
    # dominant modifiers appear in the offer label, swap to the next offer in
    # a sensible category and tell the merchant why.
    their_mods = [m.lower() for m, _n, _sh in
                  (p.get("mod_habits") or {}).get(ev.get("item") or p.get("signature_item") or "", [])]
    if their_mods and any(m in offer.get("label", "").lower() for m in their_mods):
        alt = next((o for o in goals["catalog"]
                    if o["cat"] in ("upgrade", "free_item") and o["id"] != offer["id"]
                    and not any(m in o["label"].lower() for m in their_mods)), None)
        if alt:
            offer_note = (f' The paired offer (&ldquo;{offer["label"]}&rdquo;) contradicted '
                          f'their actual habit ({", ".join(their_mods)}), so it was swapped '
                          f'for &ldquo;{alt["label"]}&rdquo;.')
            offer = alt

    subj, body, why = PACK.copy(trigger_id, ev, c, p, goals, stats, now, offer, offer_note)
    v = goals.get("_venue") or PACK.VENUE
    region = goals.get("_region", "AU")
    subj, body = _apply_tone(subj, body, goals.get("tone", "warm"), first, v["name"])
    return {
        "subject": _localise(subj, region),
        "body": _localise(body, region) + _footer(goals),
        "why": why,
        "offer_id": offer["id"],
        "offer_cat": offer["cat"],
        "offer_label": offer["label"],
        "offer_cost": offer["cost"],
    }


def run(dataset, goals, settings, sent_history=None, now=None, enabled_triggers=None,
        thresholds=None):
    """Produce a list of ready-to-send messages plus a suppression report."""
    now = now or _dt(dataset["now"])
    sent_history = sent_history or {}
    PACK.prepare(dataset)
    th = dict(PACK.DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    goals = dict(goals)
    goals["_th"] = th          # so copy/why can quote the merchant's own thresholds
    customers = dataset["customers"]
    pct_fn = _spend_percentile_fn(customers, now)
    enabled = enabled_triggers if enabled_triggers is not None else {t["id"] for t in PACK.TRIGGERS}
    ordered = [t for t in _ordered_triggers(goals) if t["id"] in enabled]

    messages, suppressed, no_match = [], [], []
    profiles = {}
    stats = PACK.venue_stats(dataset)

    for c in customers:
        p = PACK.profile(c, now, pct_fn)
        profiles[c["id"]] = p

        sup = suppression_reason(c, p, settings, sent_history, now)
        if sup:
            suppressed.append({"customer": c, "profile": p, "code": sup[0], "reason": sup[1]})
            continue

        for t in ordered:
            ok, ev = t["fn"](p, goals, th)
            if not ok:
                continue
            msg = render(t["id"], ev, c, p, goals, stats, now)
            messages.append({
                "id": f"{c['id']}_{t['id']}",
                "customer_id": c["id"],
                "customer": c,
                "profile": p,
                "trigger_id": t["id"],
                "trigger_name": t["name"],
                "trigger_rule": t["rule"],
                "offer_id": msg["offer_id"],
                "offer_cat": msg["offer_cat"],
                "offer_label": msg["offer_label"],
                "offer_cost": msg["offer_cost"],
                "subject": msg["subject"],
                "body": msg["body"],
                "why": msg["why"],
                "evidence": ev,
                "channel": settings.get("channel", "email"),
                "status": "ready",
            })
            break
        else:
            no_match.append({"customer": c, "profile": p})

    # group summary
    by_trigger = Counter(m["trigger_id"] for m in messages)
    by_cat = Counter(m["offer_cat"] for m in messages)
    sup_by = Counter(s["code"] for s in suppressed)

    return {
        "now": now.isoformat(),
        "messages": messages,
        "suppressed": suppressed,
        "no_match": no_match,
        "profiles": profiles,
        "summary": {
            "customers": len(customers),
            "ready": len(messages),
            "suppressed": len(suppressed),
            "no_match": len(no_match),
            "by_trigger": dict(by_trigger),
            "by_cat": dict(by_cat),
            "suppression_breakdown": dict(sup_by),
            "total_offer_cost_if_all_redeem": round(sum(m["offer_cost"] for m in messages), 2),
            "unique_subjects": len({m["subject"] for m in messages}),
        },
        "trigger_counts": {t["id"]: by_trigger.get(t["id"], 0) for t in PACK.TRIGGERS},
        "venue_stats": stats,
    }
