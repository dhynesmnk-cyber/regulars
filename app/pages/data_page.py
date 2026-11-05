"""Data screen: what Square gives you and what it doesn't."""

from state import dataset, result
from ui import e, page, stat
from web import html


def page_data():
    r = result()
    s = r["summary"]
    ds = dataset()
    body = ['<h1>Your data.</h1>',
            '<p class="lead">What comes out of Square, what doesn&rsquo;t, and the one thing worth '
            'fixing before spending money on any marketing tool &mdash; including this one.</p>']

    body.append('<h2>The problem worth solving first</h2>')
    body.append('<div class="grid g3">')
    body.append(stat("Customer records", f'{ds["stats"]["customers"]}', "in the directory"))
    body.append(stat("Email-sendable",
                     f'{sum(1 for c in ds["customers"] if c["email"] and c["email_consent"] and not c["unsubscribed"])}',
                     "consent on file"))
    body.append(stat("No contact details", f'{s["suppression_breakdown"].get("no_contact", 0)}',
                     "cannot be reached at all"))
    body.append('</div>')
    body.append('<div class="warn"><b>This is the highest-return change available to you and it '
                'costs nothing.</b> Square does not give you a customer&rsquo;s email or mobile '
                'number from a card payment &mdash; it gives a masked card number and sometimes a '
                'name. A customer only becomes contactable if they give you their email at the '
                'register, through loyalty signup, on your website, or when they book.</p>'
                '<p style="margin:14px 0 0">In your Square POS settings, turn on the email receipt '
                'prompt and the marketing opt-in. Then get staff to ask. Two sentences at the till '
                'move this number by ten percent a month, which is worth more than any campaign '
                'this product will ever generate.</div>')

    body.append('<h2>What we read from Square</h2>')
    body.append('<div class="card"><table><thead><tr><th>Square API</th><th>What it gives us</th>'
                '<th>What we do with it</th></tr></thead><tbody>')
    for api, gives, does in [
        ("Orders", "Every ticket: items, quantities, time, day, covers if recorded, discounts, "
                   "dine-in or takeaway.",
         "The whole product. Visit rhythm, their usual, their gap between visits, whether they add "
         "sides, whether they come with a group, which dayparts they use."),
        ("Customers", "Name, email, mobile if given, notes, birthday if captured, "
                      "<code>email_unsubscribed</code>.",
         "Who to contact, who must never be contacted, and first names for personalisation."),
        ("Catalog", "Your menu: items, prices, categories, modifiers.",
         "So a message can name a real dish, and so upsells suggest things that actually exist."),
        ("Loyalty (if subscribed)", "Points, tiers, rewards, birthdays.",
         "Birthday triggers and tier-aware offers. Only present if you pay for Square Loyalty."),
        ("CSV exports", "The same facts, as files, if you'd rather not connect anything.",
         "Everything above, via the upload on the setup screen. Column names matched tolerantly; "
         "you get a report of what was and wasn't in the file."),
    ]:
        body.append(f'<tr><td><b>{e(api)}</b></td><td class="sm">{gives}</td>'
                    f'<td class="sm">{does}</td></tr>')
    body.append('</tbody></table></div>')

    body.append('<h2>What we keep, and what we never do</h2>')
    body.append('<div class="card">')
    for status, t, d in [
        ("pass", "Nothing is sent automatically", "No scheduler, no auto-responder, no "
         "API-triggered send. A named human approves each message or batch."),
        ("pass", "Customer-facing copy carries no surveillance", "Emails name their usual and use "
         "vague time (&ldquo;a few weeks&rdquo;). Gaps, ratios, counts and percentiles exist only "
         "on the message page, for you."),
        ("pass", "Unsubscribes are permanent and portable", "Written to the suppression list in the "
         "same request. If you leave, you get the list exported to you. No trigger overrides it."),
        ("pass", "SMS is off", "Since 1 July 2026, sending under a venue&rsquo;s branded sender ID "
         "requires ACMA Register approval as an entity associate, a registered sender ID, and the "
         "venue&rsquo;s recorded consent. Email carries Spam Act obligations but no registration "
         "regime, so email is the channel until that machinery is worth building."),
        ("warn", "Automated decision-making disclosure", "From 10 December 2026 businesses must "
         "disclose automated decisions that could affect people&rsquo;s rights or interests. "
         "Segmenting customers for targeting counts. We hand you the privacy-policy clause at "
         "onboarding."),
        ("pass", "Exports are minimised", "The CSV carries email, first name, subject, body, offer, "
         "category and consent source. No spend history, no mobile number, no order details &mdash; "
         "your sender doesn&rsquo;t need them and holding less is safer."),
        ("pass", "Imports live in memory only", "An uploaded CSV is held for the running process "
         "and dropped on restart. Your customer list is not written to disk by this app."),
    ]:
        body.append(f'<div class="chk"><div class="ic {status}">{"&#10003;" if status=="pass" else "!"}</div>'
                    f'<div class="t"><b>{e(t)}</b><span>{d}</span></div></div>')
    body.append('</div>')
    body.append('<div class="warn sm"><b>Not legal advice.</b> This maps to published ACMA, OAIC and '
                'Square requirements as at November 2026. Before you send for a paying customer, '
                'have an Australian commercial lawyer review your merchant agreement, privacy '
                'policy template and consent-capture wording. Budget $3,000&ndash;$5,000 once. It '
                'is the cheapest insurance in this business.</div>')
    return page("Data", "".join(body), "/data")


def get_data(req):
    return html(page_data())
